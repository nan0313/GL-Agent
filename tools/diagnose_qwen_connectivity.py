"""Minimal, redacted, single-shot Qwen connectivity diagnosis.

The tool deliberately sits beside (rather than inside) QwenGateway.  It checks
the network layers first, then optionally makes one minimal direct request and
one formal Gateway health request.  It never retries, loops, or prints secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import (
    get_qwen_api_key,
    get_qwen_base_url,
    get_qwen_enabled,
    get_qwen_max_tokens,
    get_qwen_model,
    get_qwen_temperature,
    get_qwen_timeout_seconds,
)


PROXY_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def _redact_host(host: str | None) -> str | None:
    if not host:
        return None
    if len(host) <= 3:
        return "***"
    return f"{host[0]}***{host[-1]}"


def redact_proxy(value: str) -> dict[str, Any]:
    """Return proxy metadata without userinfo, query, or the original URL."""
    try:
        parsed = urlsplit(value)
        return {
            "scheme": parsed.scheme or None,
            "host": _redact_host(parsed.hostname),
            "port": parsed.port,
            "configured": True,
        }
    except (TypeError, ValueError):
        return {"scheme": None, "host": None, "port": None, "configured": True, "format": "invalid"}


def proxy_snapshot() -> dict[str, Any]:
    entries: dict[str, Any] = {}
    for name in PROXY_NAMES:
        value = os.getenv(name)
        if value:
            entries[name] = redact_proxy(value)
    transport_names = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"}
    return {
        "proxy_detected": any(name in entries for name in transport_names),
        "trust_env": True,
        "variables": entries,
    }


def _clean_base_url(value: str) -> str:
    value = value.strip()
    if "](" in value and value.endswith(")"):
        value = value.rsplit("](", 1)[1][:-1].strip()
    return value


def endpoint_summary(base_url: str, model: str, *, stream: bool = False) -> dict[str, Any]:
    cleaned = _clean_base_url(base_url)
    parsed = urlsplit(cleaned)
    path = parsed.path or ""
    lowered = path.lower()
    duplicate = lowered.count("/v1") > 1 or lowered.count("/chat/completions") > 1
    if duplicate:
        path_status = "duplicate_path"
    elif not parsed.scheme or not parsed.hostname:
        path_status = "invalid"
    elif not path or path == "/":
        path_status = "empty_path"
    else:
        path_status = "valid"
    resolved_path = path.rstrip("/") or "/"
    if not resolved_path.endswith("/chat/completions"):
        resolved_path = f"{resolved_path.rstrip('/')}/chat/completions"
    try:
        port = parsed.port
    except ValueError:
        port = None
        path_status = "invalid"
    if port is None and parsed.scheme == "https":
        port = 443
    elif port is None and parsed.scheme == "http":
        port = 80
    return {
        "configured": bool(cleaned),
        "scheme": parsed.scheme or None,
        "host": _redact_host(parsed.hostname),
        "port": port,
        "api_path": resolved_path,
        "path_status": path_status,
        "model": model,
        "stream": bool(stream),
    }


def safe_error_type(exc: BaseException) -> str:
    if isinstance(exc, socket.gaierror):
        return "dns_error"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
        return "unreachable"
    name = type(exc).__name__.lower()
    if "certificate" in name or "ssl" in name:
        return "tls_error"
    if "timeout" in name:
        return "timeout"
    return "network_error"


def dns_probe(host: str, port: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = {str(info[4][0]) for info in infos if info and info[4]}
        return {"status": "success", "latency_ms": int((time.perf_counter() - started) * 1000), "addresses": len(addresses)}
    except Exception as exc:  # noqa: BLE001 - output is intentionally classified
        return {"status": "failed", "latency_ms": int((time.perf_counter() - started) * 1000), "error": safe_error_type(exc)}


def tcp_probe(host: str, port: int, timeout: float = 3.0) -> dict[str, Any]:
    started = time.perf_counter()
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        return {"status": "success", "latency_ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "latency_ms": int((time.perf_counter() - started) * 1000), "error": safe_error_type(exc)}
    finally:
        if sock is not None:
            sock.close()


def tls_probe(host: str, port: int, timeout: float = 3.0) -> dict[str, Any]:
    started = time.perf_counter()
    raw = None
    wrapped = None
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
        context = ssl.create_default_context()
        wrapped = context.wrap_socket(raw, server_hostname=host)
        return {
            "status": "success",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "protocol": wrapped.version(),
            "certificate_verified": True,
            "sni": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "latency_ms": int((time.perf_counter() - started) * 1000), "error": safe_error_type(exc), "certificate_verified": False}
    finally:
        if wrapped is not None:
            wrapped.close()
        elif raw is not None:
            raw.close()


def _http_error_class(status_code: int) -> str | None:
    if status_code in {401, 403}:
        return "AUTH_FAILURE"
    if status_code == 404:
        return "MODEL_NOT_FOUND"
    if status_code == 429:
        return "RATE_LIMITED_OR_QUEUED"
    if 400 <= status_code < 500:
        return "CONFIGURATION_ERROR"
    return None


def _read_phase(*, headers_received: bool, first_body_byte: bool) -> str:
    if not headers_received:
        return "waiting_response_headers"
    if not first_body_byte:
        return "waiting_first_body_byte"
    return "reading_response_body"


def direct_request(base_url: str, model: str, api_key: str, *, trust_env: bool = True) -> dict[str, Any]:
    """Make one and only one minimal non-streaming request."""
    cleaned = _clean_base_url(base_url)
    parsed = urlsplit(cleaned)
    path = parsed.path.rstrip("/") or ""
    url = cleaned if path.endswith("/chat/completions") else f"{cleaned.rstrip('/')}/chat/completions"
    timeout = httpx.Timeout(5.0, connect=3.0, write=3.0, pool=3.0)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": [{"role": "user", "content": "Respond with exactly: OK"}], "temperature": 0.0, "max_tokens": 4, "stream": False}
    started = time.perf_counter()
    headers_received = False
    first_body_byte = False
    body = bytearray()
    result: dict[str, Any] = {"status": "failed", "request_started": True, "response_headers_received": False, "first_body_byte_received": False, "request_completed": False, "retry_count": 0}
    try:
        with httpx.Client(timeout=timeout, trust_env=trust_env) as client:
            with client.stream("POST", url, headers=headers, json=payload) as response:
                headers_received = True
                result["response_headers_received"] = True
                result["headers_latency_ms"] = int((time.perf_counter() - started) * 1000)
                result["http_status"] = response.status_code
                for chunk in response.iter_bytes():
                    if chunk and not first_body_byte:
                        first_body_byte = True
                        result["first_body_byte_received"] = True
                        result["first_byte_latency_ms"] = int((time.perf_counter() - started) * 1000)
                    if len(body) < 1_000_000:
                        body.extend(chunk[: 1_000_000 - len(body)])
        result["request_completed"] = True
        result["total_latency_ms"] = int((time.perf_counter() - started) * 1000)
        raw = bytes(body)
        result["response_present"] = bool(raw)
        result["response_chars"] = len(raw.decode("utf-8", errors="replace"))
        try:
            parsed_body = json.loads(raw.decode("utf-8")) if raw else {}
            choice = (parsed_body.get("choices") or [{}])[0]
            result["finish_reason"] = choice.get("finish_reason")
            usage = parsed_body.get("usage") or {}
            result["usage_tokens"] = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens") if key in usage}
            content = ((choice.get("message") or {}).get("content")) if isinstance(choice, dict) else None
            if not isinstance(content, str) or not content.strip():
                result["status"] = "failed"
                result["error"] = "invalid_response"
                result["classification_hint"] = "CONFIGURATION_ERROR"
        except (ValueError, TypeError, AttributeError):
            result["finish_reason"] = None
            result["usage_tokens"] = {}
        if "classification_hint" not in result:
            result["classification_hint"] = _http_error_class(int(result.get("http_status", 0))) or ("INCONCLUSIVE" if int(result.get("http_status", 0)) >= 300 else None)
        if result.get("error") != "invalid_response":
            result["status"] = "success" if 200 <= int(result.get("http_status", 0)) < 300 else "failed"
        return result
    except httpx.ConnectTimeout:
        result.update({"error": "connect_timeout", "classification_hint": "TCP_CONNECTIVITY_FAILURE"})
    except httpx.ReadTimeout:
        result.update({"error": "read_timeout", "read_phase": _read_phase(headers_received=headers_received, first_body_byte=first_body_byte), "classification_hint": "PROVIDER_READ_TIMEOUT"})
    except httpx.WriteTimeout:
        result.update({"error": "write_timeout", "classification_hint": "PROVIDER_READ_TIMEOUT"})
    except httpx.PoolTimeout:
        result.update({"error": "pool_timeout", "classification_hint": "PROXY_INTERFERENCE" if trust_env else "INCONCLUSIVE"})
    except httpx.HTTPError as exc:
        result.update({"error": safe_error_type(exc), "classification_hint": "PROXY_INTERFERENCE" if trust_env else "INCONCLUSIVE"})
    result["total_latency_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def gateway_health() -> tuple[dict[str, Any], int]:
    # Lazy import keeps this standalone diagnostic from changing application
    # import order (the production package has legacy compatibility imports).
    from app.qwen_gateway.gateway import get_qwen_gateway
    from app.qwen_gateway.telemetry import default_qwen_ledger

    before = len(default_qwen_ledger.list())
    started = time.perf_counter()
    try:
        health = get_qwen_gateway().health_check()
        data = health.model_dump(exclude_none=False)
    except Exception as exc:  # noqa: BLE001 - report safe classification only
        data = {"available": False, "last_status": "failed", "error_code": "QWEN_UNAVAILABLE", "error_type": safe_error_type(exc)}
    items = default_qwen_ledger.list()
    new_items = items[before:]
    invocation = new_items[-1] if new_items else None
    attempted = 1 if invocation and invocation.get("invocation_source", "real") == "real" else 0
    return {
        "status": "success" if data.get("available") else "failed",
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "health": {key: data.get(key) for key in ("configured", "available", "circuit_state", "last_status", "last_latency_ms", "timeout_rate", "error_code", "model")},
        "invocation": {key: invocation.get(key) for key in ("status", "latency_ms", "retry_count", "circuit_state", "error_code", "invocation_source")} if invocation else None,
        "ledger_generated": bool(invocation),
    }, attempted


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return None


def run_diagnosis(*, network_only: bool = False, run_direct: bool = True, run_health: bool = True, ignore_env_proxy: bool = False) -> dict[str, Any]:
    base_url = get_qwen_base_url()
    model = get_qwen_model()
    endpoint = endpoint_summary(base_url, model, stream=False)
    proxy = proxy_snapshot()
    proxy["trust_env"] = not ignore_env_proxy
    report: dict[str, Any] = {"diagnostic_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "commit": git_commit(), "configuration": {**endpoint, "enabled": get_qwen_enabled(), "api_key_configured": bool(get_qwen_api_key()), "timeout_seconds": get_qwen_timeout_seconds(), "temperature": get_qwen_temperature(), "max_tokens": get_qwen_max_tokens()}, "proxy": proxy, "real_call_count": 0}
    if not endpoint.get("configured") or endpoint.get("path_status") in {"invalid", "duplicate_path"}:
        report["dns"] = {"status": "skipped"}
        report["tcp"] = {"status": "skipped"}
        report["tls"] = {"status": "skipped"}
    else:
        parsed = urlsplit(_clean_base_url(base_url))
        port = endpoint["port"]
        report["dns"] = dns_probe(parsed.hostname, port)
        report["tcp"] = tcp_probe(parsed.hostname, port) if report["dns"]["status"] == "success" else {"status": "skipped"}
        report["tls"] = tls_probe(parsed.hostname, port) if parsed.scheme == "https" and report["tcp"]["status"] == "success" else ({"status": "skipped"} if parsed.scheme != "https" else {"status": "skipped"})
    if not network_only and endpoint.get("configured") and report.get("tcp", {}).get("status") == "success" and (endpoint.get("scheme") != "https" or report.get("tls", {}).get("status") == "success"):
        if run_direct:
            if not get_qwen_enabled() or not get_qwen_api_key():
                report["direct_http"] = {"status": "skipped", "reason": "disabled_or_unconfigured"}
            else:
                report["direct_http"] = direct_request(base_url, model, get_qwen_api_key(), trust_env=not ignore_env_proxy)
                report["real_call_count"] += 1
        if run_health:
            health, calls = gateway_health()
            report["gateway_health"] = health
            report["real_call_count"] += calls
    else:
        report["direct_http"] = {"status": "skipped", "reason": "network_prerequisite_failed"}
        report["gateway_health"] = {"status": "skipped", "reason": "network_prerequisite_failed"}
    report["diagnostic_classification"] = classify_report(report)
    return report


def classify_report(report: dict[str, Any]) -> str:
    config = report.get("configuration", {})
    if config.get("path_status") in {"invalid", "duplicate_path"}:
        return "CONFIGURATION_ERROR"
    if report.get("dns", {}).get("status") == "failed":
        return "DNS_FAILURE"
    if report.get("tcp", {}).get("status") == "failed":
        return "TCP_CONNECTIVITY_FAILURE"
    if report.get("tls", {}).get("status") == "failed":
        return "TLS_FAILURE"
    direct = report.get("direct_http", {})
    hint = direct.get("classification_hint")
    if hint in {"AUTH_FAILURE", "MODEL_NOT_FOUND", "RATE_LIMITED_OR_QUEUED", "CONFIGURATION_ERROR", "PROVIDER_READ_TIMEOUT"}:
        return hint
    health = report.get("gateway_health", {})
    if direct.get("status") == "success" and health.get("status") == "failed":
        return "GATEWAY_INTEGRATION_ERROR"
    if direct.get("status") == "success" and health.get("status") == "success":
        return "NOT_REPRODUCED"
    if direct.get("status") == "skipped" and health.get("status") == "success":
        return "NOT_REPRODUCED"
    if report.get("proxy", {}).get("proxy_detected") and direct.get("error") in {"network_error", "pool_timeout"}:
        return "PROXY_INTERFERENCE"
    if not config.get("enabled") or not config.get("api_key_configured"):
        return "INCONCLUSIVE"
    return "INCONCLUSIVE"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one redacted Qwen connectivity diagnosis.")
    parser.add_argument("--network-only", action="store_true", help="Only run configuration/DNS/TCP/TLS probes.")
    parser.add_argument("--direct-request", action="store_true", help="Run the one minimal direct HTTP request.")
    parser.add_argument("--gateway-health", action="store_true", help="Run the one formal Gateway health request.")
    parser.add_argument("--ignore-env-proxy", action="store_true", help="Use trust_env=false for the direct request.")
    parser.add_argument("--output", type=Path, help="Write the redacted JSON report to this path.")
    args = parser.parse_args()
    explicit = args.direct_request or args.gateway_health
    report = run_diagnosis(network_only=args.network_only, run_direct=args.direct_request or not explicit, run_health=args.gateway_health or not explicit, ignore_env_proxy=args.ignore_env_proxy)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
