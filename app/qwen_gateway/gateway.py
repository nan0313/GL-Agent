import asyncio
from datetime import datetime, timezone
import inspect
import json
from time import perf_counter
from typing import Any, Callable

import httpx

from app.config import get_qwen_api_key, get_qwen_base_url, get_qwen_enable_thinking, get_qwen_enabled, get_qwen_max_tokens, get_qwen_model, get_qwen_temperature, get_qwen_timeout_seconds
from app.product.answer_stream import emit_answer_delta, has_answer_delta_stream

from .circuit_breaker import CircuitBreaker
from .models import QwenGatewayError, QwenHealth, QwenInvocation, TimeoutBudget
from .prompt_budget import PromptBudget
from .telemetry import default_qwen_ledger


_ORIGINAL_HTTPX_POST = httpx.post
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class QwenGateway:
    """Single model-service boundary shared by every Qwen purpose."""

    def __init__(self, *, client: httpx.Client | None = None, async_client: httpx.AsyncClient | None = None, breaker: CircuitBreaker | None = None, prompt_budget: PromptBudget | None = None, ledger=None, sleep: Callable[[float], Any] | None = None, invocation_source: str = "real") -> None:
        self.client = client
        self.async_client = async_client
        self.breaker = breaker or CircuitBreaker()
        self.prompt_budget = prompt_budget or PromptBudget()
        self.ledger = ledger or default_qwen_ledger
        self._sleep = sleep or (lambda seconds: asyncio.sleep(seconds))
        self._owns_client = client is None
        self._owns_async_client = async_client is None
        self.invocation_source = invocation_source
        self._last: QwenInvocation | None = None

    def invoke_structured(self, messages: list[dict[str, str]], *, purpose: str = "unknown", request_id: str = "", retry: bool = True) -> str:
        return self._invoke(messages, purpose=purpose, request_id=request_id, retry=retry)

    def classify(self, messages, *, request_id: str = "") -> str:
        return self.invoke_structured(messages, purpose="router", request_id=request_id)

    def plan(self, messages, *, request_id: str = "", complex_plan: bool = False) -> str:
        return self.invoke_structured(messages, purpose="complex_planner" if complex_plan else "semantic_planner", request_id=request_id)

    def answer(self, messages, *, request_id: str = "", purpose: str = "general_chat") -> str:
        return self.invoke_structured(messages, purpose=purpose, request_id=request_id)

    def summarize(self, messages, *, request_id: str = "") -> str:
        return self.invoke_structured(messages, purpose="summarizer", request_id=request_id)

    def repair(self, messages, *, request_id: str = "") -> str:
        return self.invoke_structured(messages, purpose="schema_repair", request_id=request_id, retry=False)

    def record_schema_failure(self, messages, *, purpose: str, request_id: str = "", invocation_source: str | None = None) -> None:
        """Record a model-text schema failure before a single repair attempt."""
        _, budget_info = self._budget(messages, purpose)
        self._record(request_id, purpose, "failed", 0, 0, False, "QWEN_SCHEMA_FAILED", budget_info, invocation_source=invocation_source)

    async def invoke_structured_async(self, messages, *, purpose: str = "unknown", request_id: str = "", retry: bool = True) -> str:
        budgeted, budget_info = self._budget(messages, purpose)
        if not get_qwen_enabled() or not get_qwen_api_key():
            self._record(request_id, purpose, "fallback", 0, 0, True, "QWEN_DISABLED", budget_info)
            raise QwenGatewayError("QWEN_DISABLED", "模型服务暂未启用", purpose=purpose)
        if not self.breaker.allow(purpose):
            self._record(request_id, purpose, "circuit_open", 0, 0, True, "QWEN_CIRCUIT_OPEN", budget_info)
            raise QwenGatewayError("QWEN_CIRCUIT_OPEN", "模型服务暂时不可用，请稍后重试", purpose=purpose)
        budget = self._budget_for(purpose)
        overall_started = perf_counter()
        for attempt in range(2 if retry else 1):
            if perf_counter() - overall_started >= budget.overall_deadline:
                self._record(request_id, purpose, "timeout", int((perf_counter()-overall_started)*1000), attempt, True, "QWEN_DEADLINE_EXCEEDED", budget_info)
                raise QwenGatewayError("QWEN_DEADLINE_EXCEEDED", "模型服务请求已超过时间限制", purpose=purpose)
            started = perf_counter()
            try:
                content = await asyncio.wait_for(self._post_async(budgeted, budget), timeout=max(0.01, budget.overall_deadline - (perf_counter() - overall_started)))
                self.breaker.record_success(purpose)
                self._record(request_id, purpose, "success", int((perf_counter()-started)*1000), attempt, False, None, budget_info)
                return content
            except QwenGatewayError as exc:
                if perf_counter() - overall_started >= budget.overall_deadline:
                    self._record(request_id, purpose, "timeout", int((perf_counter()-overall_started)*1000), attempt, True, "QWEN_DEADLINE_EXCEEDED", budget_info)
                    raise QwenGatewayError("QWEN_DEADLINE_EXCEEDED", "模型服务请求已超过时间限制", purpose=purpose) from exc
                if retry and attempt == 0 and exc.retryable:
                    if perf_counter() - overall_started >= budget.overall_deadline:
                        self._record(request_id, purpose, "timeout", int((perf_counter()-overall_started)*1000), attempt, True, "QWEN_DEADLINE_EXCEEDED", budget_info)
                        raise QwenGatewayError("QWEN_DEADLINE_EXCEEDED", "模型服务请求已超过时间限制", purpose=purpose)
                    self.breaker.record_failure(purpose)
                    self._record(request_id, purpose, "retry", int((perf_counter()-started)*1000), attempt, False, exc.code, budget_info)
                    await asyncio.sleep(min(0.15, max(0.01, budget.overall_deadline / 20)))
                    continue
                self.breaker.record_failure(purpose)
                self._record(request_id, purpose, "timeout" if "TIMEOUT" in exc.code else "failed", int((perf_counter()-started)*1000), attempt, True, exc.code, budget_info)
                raise
            except asyncio.TimeoutError as exc:
                self.breaker.record_failure(purpose)
                self._record(request_id, purpose, "timeout", int((perf_counter()-overall_started)*1000), attempt, True, "QWEN_DEADLINE_EXCEEDED", budget_info)
                raise QwenGatewayError("QWEN_DEADLINE_EXCEEDED", "模型服务请求已超过时间限制", purpose=purpose) from exc
        raise QwenGatewayError("QWEN_UNAVAILABLE", "模型服务暂时不可用", purpose=purpose)

    def health_check(self) -> QwenHealth:
        start = perf_counter()
        recent = self.ledger.list()
        timeout_items = [item for item in recent if item.get("status") == "timeout" or str(item.get("error_code", "")).endswith("TIMEOUT")]
        timeout_rate = (len(timeout_items) / len(recent)) if recent else 0.0
        configured = bool(get_qwen_enabled() and get_qwen_api_key() and get_qwen_base_url().strip())
        if not configured:
            return QwenHealth(configured=bool(get_qwen_api_key()), available=False, circuit_state=self.breaker.state("health_check"), last_status="disabled", timeout_rate=timeout_rate, error_code="QWEN_DISABLED", model=get_qwen_model(), model_endpoint_configured=bool(get_qwen_base_url().strip()), last_checked_at=datetime.now(timezone.utc).isoformat())
        try:
            self.invoke_structured([{"role": "system", "content": "Return JSON: {ok:true}."}, {"role": "user", "content": "health"}], purpose="health_check", retry=False)
            return QwenHealth(configured=True, available=True, circuit_state=self.breaker.state("health_check"), last_status="success", last_latency_ms=int((perf_counter()-start)*1000), timeout_rate=timeout_rate, model=get_qwen_model(), model_endpoint_configured=True, last_checked_at=datetime.now(timezone.utc).isoformat())
        except QwenGatewayError as exc:
            return QwenHealth(configured=True, available=False, circuit_state=self.breaker.state("health_check"), last_status="failed", last_latency_ms=int((perf_counter()-start)*1000), timeout_rate=timeout_rate, error_code=exc.code, model=get_qwen_model(), model_endpoint_configured=True, last_checked_at=datetime.now(timezone.utc).isoformat())

    def status_snapshot(self) -> dict[str, Any]:
        """Return local provider state without sending a network request."""
        recent = self.ledger.list()
        latest = recent[-1] if recent else {}
        configured = bool(get_qwen_enabled() and get_qwen_api_key() and get_qwen_base_url().strip())
        latest_status = str(latest.get("status") or ("unknown" if configured else "disabled"))
        latest_error = latest.get("error_code") or (None if configured else "QWEN_DISABLED")
        return {
            "configured": configured,
            "available": latest_status == "success",
            "provider_status": "available" if latest_status == "success" else ("disabled" if not configured else "unavailable"),
            "last_status": latest_status,
            "last_latency_ms": int(latest.get("latency_ms") or 0),
            "error_code": latest_error,
            "circuit_state": self.breaker.state(str(latest.get("purpose") or "health_check")),
            "model": get_qwen_model(),
            "network_call": False,
        }

    async def aclose(self) -> None:
        if self.async_client is not None and self._owns_async_client:
            await self.async_client.aclose()
            self.async_client = None
        if self.client is not None and self._owns_client:
            self.client.close()
            self.client = None

    def close(self) -> None:
        if self.client is not None and self._owns_client:
            self.client.close()
            self.client = None

    def _invoke(self, messages, *, purpose: str, request_id: str, retry: bool) -> str:
        budgeted, budget_info = self._budget(messages, purpose)
        if not get_qwen_enabled() or not get_qwen_api_key():
            self._record(request_id, purpose, "fallback", 0, 0, True, "QWEN_DISABLED", budget_info)
            raise QwenGatewayError("QWEN_DISABLED", "模型服务暂未启用", purpose=purpose)
        if not get_qwen_base_url().strip():
            self._record(request_id, purpose, "fallback", 0, 0, True, "QWEN_UNAVAILABLE", budget_info)
            raise QwenGatewayError("QWEN_UNAVAILABLE", "模型服务配置不可用", purpose=purpose)
        if not self.breaker.allow(purpose):
            self._record(request_id, purpose, "circuit_open", 0, 0, True, "QWEN_CIRCUIT_OPEN", budget_info)
            raise QwenGatewayError("QWEN_CIRCUIT_OPEN", "模型服务暂时不可用，请稍后重试", purpose=purpose)
        budget = self._budget_for(purpose)
        overall_started = perf_counter()
        for attempt in range(2 if retry else 1):
            if perf_counter() - overall_started >= budget.overall_deadline:
                self._record(request_id, purpose, "timeout", int((perf_counter()-overall_started)*1000), attempt, True, "QWEN_DEADLINE_EXCEEDED", budget_info)
                raise QwenGatewayError("QWEN_DEADLINE_EXCEEDED", "模型服务请求已超过时间限制", purpose=purpose)
            started = perf_counter()
            try:
                remaining = max(0.01, budget.overall_deadline - (perf_counter() - overall_started))
                attempt_budget = TimeoutBudget(
                    budget.connect_timeout,
                    min(budget.read_timeout, remaining),
                    budget.write_timeout,
                    budget.pool_timeout,
                    remaining,
                )
                content = (
                    self._post_streaming(budgeted, attempt_budget)
                    if purpose in {"general_chat", "rag_answer", "summarizer"} and has_answer_delta_stream()
                    else self._post(budgeted, attempt_budget)
                )
                self.breaker.record_success(purpose)
                self._record(request_id, purpose, "success", int((perf_counter()-started)*1000), attempt, False, None, budget_info)
                return content
            except QwenGatewayError as exc:
                if perf_counter() - overall_started >= budget.overall_deadline:
                    self._record(request_id, purpose, "timeout", int((perf_counter()-overall_started)*1000), attempt, True, "QWEN_DEADLINE_EXCEEDED", budget_info)
                    raise QwenGatewayError("QWEN_DEADLINE_EXCEEDED", "模型服务请求已超过时间限制", purpose=purpose) from exc
                if retry and attempt == 0 and exc.retryable:
                    if perf_counter() - overall_started >= budget.overall_deadline:
                        self._record(request_id, purpose, "timeout", int((perf_counter()-overall_started)*1000), attempt, True, "QWEN_DEADLINE_EXCEEDED", budget_info)
                        raise QwenGatewayError("QWEN_DEADLINE_EXCEEDED", "模型服务请求已超过时间限制", purpose=purpose)
                    self._record(request_id, purpose, "retry", int((perf_counter()-started)*1000), attempt, False, exc.code, budget_info)
                    self.breaker.record_failure(purpose)
                    delay = min(0.15, max(0.01, budget.overall_deadline / 20))
                    result = self._sleep(delay)
                    if inspect.isawaitable(result):
                        try:
                            asyncio.get_running_loop()
                        except RuntimeError:
                            asyncio.run(result)
                    continue
                self.breaker.record_failure(purpose)
                self._record(request_id, purpose, "timeout" if "TIMEOUT" in exc.code or "DEADLINE" in exc.code else "failed", int((perf_counter()-started)*1000), attempt, True, exc.code, budget_info)
                raise
        raise QwenGatewayError("QWEN_UNAVAILABLE", "模型服务暂时不可用", purpose=purpose)

    def _post(self, messages, budget: TimeoutBudget) -> str:
        timeout = httpx.Timeout(budget.read_timeout, connect=budget.connect_timeout, write=budget.write_timeout, pool=budget.pool_timeout)
        payload = {"model": get_qwen_model(), "messages": messages, "temperature": get_qwen_temperature(), "max_tokens": get_qwen_max_tokens(), "enable_thinking": get_qwen_enable_thinking()}
        headers = {"Authorization": f"Bearer {get_qwen_api_key()}", "Content-Type": "application/json"}
        try:
            # Keep legacy monkeypatch compatibility for the existing unit suite;
            # production uses the shared connection-pooled Client below.
            if httpx.post is not _ORIGINAL_HTTPX_POST:
                response = httpx.post(self._url(), headers=headers, json=payload, timeout=get_qwen_timeout_seconds())
            else:
                response = (self.client or self._shared_client()).post(self._url(), headers=headers, json=payload, timeout=timeout)
        except httpx.ConnectTimeout as exc:
            raise QwenGatewayError("QWEN_CONNECT_TIMEOUT", "模型服务连接超时", retryable=True) from exc
        except httpx.ReadTimeout as exc:
            raise QwenGatewayError("QWEN_READ_TIMEOUT", "模型服务响应超时", retryable=True) from exc
        except httpx.WriteTimeout as exc:
            raise QwenGatewayError("QWEN_WRITE_TIMEOUT", "模型服务发送超时") from exc
        except httpx.PoolTimeout as exc:
            raise QwenGatewayError("QWEN_POOL_TIMEOUT", "模型服务连接池繁忙", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise QwenGatewayError("QWEN_UNAVAILABLE", "模型服务暂时不可用", retryable=True) from exc
        status = getattr(response, "status_code", 200)
        if status in {401, 403}:
            raise QwenGatewayError("QWEN_AUTH_FAILED", "模型服务认证失败", status_code=status)
        if status == 404:
            raise QwenGatewayError("QWEN_MODEL_NOT_FOUND", "模型服务不存在", status_code=status)
        if status in _RETRYABLE_STATUS:
            raise QwenGatewayError("QWEN_RATE_LIMITED" if status == 429 else "QWEN_SERVER_ERROR", "模型服务暂时繁忙", retryable=True, status_code=status)
        if status < 200 or status >= 300:
            raise QwenGatewayError("QWEN_SERVER_ERROR", "模型服务返回错误", status_code=status)
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise QwenGatewayError("QWEN_INVALID_RESPONSE", "模型服务返回格式无效") from exc
        if not isinstance(content, str) or not content.strip():
            raise QwenGatewayError("QWEN_INVALID_RESPONSE", "模型服务返回内容为空")
        return content

    def _post_streaming(self, messages, budget: TimeoutBudget) -> str:
        timeout = httpx.Timeout(
            budget.read_timeout,
            connect=budget.connect_timeout,
            write=budget.write_timeout,
            pool=budget.pool_timeout,
        )
        payload = {
            "model": get_qwen_model(),
            "messages": messages,
            "temperature": get_qwen_temperature(),
            "max_tokens": get_qwen_max_tokens(),
            "enable_thinking": get_qwen_enable_thinking(),
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {get_qwen_api_key()}", "Content-Type": "application/json"}
        if httpx.post is not _ORIGINAL_HTTPX_POST:
            return self._post(messages, budget)
        try:
            with (self.client or self._shared_client()).stream(
                "POST",
                self._url(),
                headers=headers,
                json=payload,
                timeout=timeout,
            ) as response:
                status = getattr(response, "status_code", 200)
                if status in {401, 403}:
                    raise QwenGatewayError("QWEN_AUTH_FAILED", "模型服务认证失败", status_code=status)
                if status == 404:
                    raise QwenGatewayError("QWEN_MODEL_NOT_FOUND", "模型服务不存在", status_code=status)
                if status in _RETRYABLE_STATUS:
                    raise QwenGatewayError(
                        "QWEN_RATE_LIMITED" if status == 429 else "QWEN_SERVER_ERROR",
                        "模型服务暂时繁忙",
                        retryable=True,
                        status_code=status,
                    )
                if status < 200 or status >= 300:
                    raise QwenGatewayError("QWEN_SERVER_ERROR", "模型服务返回错误", status_code=status)
                parts: list[str] = []
                for line in response.iter_lines():
                    value = str(line or "").strip()
                    if not value or not value.startswith("data:"):
                        continue
                    data = value[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        item = json.loads(data)
                        choice = (item.get("choices") or [{}])[0]
                        delta = (choice.get("delta") or {}).get("content")
                        if delta is None:
                            delta = (choice.get("message") or {}).get("content")
                    except (AttributeError, IndexError, TypeError, ValueError):
                        continue
                    if isinstance(delta, list):
                        delta = "".join(
                            str(part.get("text") or "")
                            for part in delta
                            if isinstance(part, dict) and part.get("type") in {None, "text"}
                        )
                    if not isinstance(delta, str) or not delta:
                        continue
                    parts.append(delta)
                    emit_answer_delta(delta)
                content = "".join(parts)
        except httpx.ConnectTimeout as exc:
            raise QwenGatewayError("QWEN_CONNECT_TIMEOUT", "模型服务连接超时", retryable=True) from exc
        except httpx.ReadTimeout as exc:
            raise QwenGatewayError("QWEN_READ_TIMEOUT", "模型服务响应超时", retryable=True) from exc
        except httpx.WriteTimeout as exc:
            raise QwenGatewayError("QWEN_WRITE_TIMEOUT", "模型服务发送超时") from exc
        except httpx.PoolTimeout as exc:
            raise QwenGatewayError("QWEN_POOL_TIMEOUT", "模型服务连接池繁忙", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise QwenGatewayError("QWEN_UNAVAILABLE", "模型服务暂时不可用", retryable=True) from exc
        if not content.strip():
            raise QwenGatewayError("QWEN_INVALID_RESPONSE", "模型服务返回内容为空")
        return content

    def _shared_client(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
        return self.client

    def _shared_async_client(self) -> httpx.AsyncClient:
        if self.async_client is None:
            self.async_client = httpx.AsyncClient(limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
        return self.async_client

    async def _post_async(self, messages, budget: TimeoutBudget) -> str:
        timeout = httpx.Timeout(budget.read_timeout, connect=budget.connect_timeout, write=budget.write_timeout, pool=budget.pool_timeout)
        payload = {"model": get_qwen_model(), "messages": messages, "temperature": get_qwen_temperature(), "max_tokens": get_qwen_max_tokens(), "enable_thinking": get_qwen_enable_thinking()}
        headers = {"Authorization": f"Bearer {get_qwen_api_key()}", "Content-Type": "application/json"}
        try:
            response = await self._shared_async_client().post(self._url(), headers=headers, json=payload, timeout=timeout)
        except httpx.ConnectTimeout as exc:
            raise QwenGatewayError("QWEN_CONNECT_TIMEOUT", "模型服务连接超时", retryable=True) from exc
        except httpx.ReadTimeout as exc:
            raise QwenGatewayError("QWEN_READ_TIMEOUT", "模型服务响应超时", retryable=True) from exc
        except httpx.WriteTimeout as exc:
            raise QwenGatewayError("QWEN_WRITE_TIMEOUT", "模型服务发送超时") from exc
        except httpx.PoolTimeout as exc:
            raise QwenGatewayError("QWEN_POOL_TIMEOUT", "模型服务连接池繁忙", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise QwenGatewayError("QWEN_UNAVAILABLE", "模型服务暂时不可用", retryable=True) from exc
        return self._parse_response(response)

    def _parse_response(self, response) -> str:
        status = getattr(response, "status_code", 200)
        if status in {401, 403}:
            raise QwenGatewayError("QWEN_AUTH_FAILED", "模型服务认证失败", status_code=status)
        if status == 404:
            raise QwenGatewayError("QWEN_MODEL_NOT_FOUND", "模型服务不存在", status_code=status)
        if status in _RETRYABLE_STATUS:
            raise QwenGatewayError("QWEN_RATE_LIMITED" if status == 429 else "QWEN_SERVER_ERROR", "模型服务暂时繁忙", retryable=True, status_code=status)
        if status < 200 or status >= 300:
            raise QwenGatewayError("QWEN_SERVER_ERROR", "模型服务返回错误", status_code=status)
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise QwenGatewayError("QWEN_INVALID_RESPONSE", "模型服务返回格式无效") from exc
        if not isinstance(content, str) or not content.strip():
            raise QwenGatewayError("QWEN_INVALID_RESPONSE", "模型服务返回内容为空")
        return content

    def _url(self) -> str:
        base = get_qwen_base_url().strip()
        if "](" in base and base.endswith(")"):
            base = base.rsplit("](", 1)[1][:-1].strip()
        return base if base.endswith("/chat/completions") else f"{base.rstrip('/')}/chat/completions"

    def _budget(self, messages, purpose):
        result = self.prompt_budget.messages(messages, purpose=purpose)
        return result.messages, self.prompt_budget.audit(result)

    def _budget_for(self, purpose: str) -> TimeoutBudget:
        configured_timeout = get_qwen_timeout_seconds()
        if configured_timeout <= 0:
            raise ValueError("QWEN_TIMEOUT_SECONDS must be greater than zero.")
        phase_limits = {
            "router": (2, 5, 2),
            "semantic_planner": (3, 5, 2),
            "complex_planner": (3, 5, 2),
            "rag_answer": (3, 5, 2),
            "general_chat": (3, 5, 2),
            "summarizer": (2, 5, 2),
            "schema_repair": (2, 5, 2),
            "health_check": (1, 3, 1),
        }
        connect_timeout, write_timeout, pool_timeout = phase_limits.get(purpose, (3, 5, 2))
        return TimeoutBudget(
            connect_timeout=min(connect_timeout, configured_timeout),
            read_timeout=configured_timeout,
            write_timeout=min(write_timeout, configured_timeout),
            pool_timeout=min(pool_timeout, configured_timeout),
            overall_deadline=configured_timeout,
        )

    def _record(self, request_id, purpose, status, latency, retry_count, fallback, error_code, budget_info, invocation_source: str | None = None):
        item = QwenInvocation(request_id=request_id, purpose=purpose, model=get_qwen_model(), status=status, latency_ms=latency, retry_count=retry_count, circuit_state=self.breaker.state(purpose), prompt_chars=budget_info.get("final_chars", 0), prompt_budget=budget_info, fallback_used=fallback, error_code=error_code, invocation_source=invocation_source or self.invocation_source)
        self._last = item
        self.ledger.add(item)


_default_gateway: QwenGateway | None = None


def get_qwen_gateway() -> QwenGateway:
    global _default_gateway
    if _default_gateway is None:
        _default_gateway = QwenGateway()
    return _default_gateway
