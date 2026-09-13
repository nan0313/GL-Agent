import os
import sys
from pathlib import Path
from typing import Any

import yaml


SUPPORTED_WORKFLOW_PROVIDERS = {"python", "langgraph"}
DEFAULT_WORKFLOW_PROVIDER = "langgraph"
SUPPORTED_LLM_PLANNER_PROVIDERS = {"mock", "lcel_mock", "qwen", "dawatt"}
DEFAULT_LLM_PLANNER_PROVIDER = "lcel_mock"
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_QWEN_MODEL = "qwen-plus"
DEFAULT_QWEN_API_KEY = ""
DEFAULT_QWEN_TIMEOUT_SECONDS = 30.0
DEFAULT_QWEN_TEMPERATURE = 0.0
DEFAULT_QWEN_MAX_TOKENS = 1024
DEFAULT_QWEN_ENABLE_THINKING = False
QWEN_ENABLED_VALUES = {"true", "1", "yes"}
LOCAL_LLM_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "local_llm_config.yaml"
SUPPORTED_TRACE_STORE_PROVIDERS = {"memory", "sqlite"}
DEFAULT_TRACE_STORE_PROVIDER = "memory"
DEFAULT_TRACE_SQLITE_PATH = "data/traces.db"


def get_workflow_provider() -> str:
    provider = os.getenv(
        "AGENT_WORKFLOW_PROVIDER",
        DEFAULT_WORKFLOW_PROVIDER,
    ).strip().lower()
    if provider not in SUPPORTED_WORKFLOW_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_WORKFLOW_PROVIDERS))
        raise ValueError(
            f"Unsupported AGENT_WORKFLOW_PROVIDER: {provider}. "
            f"Supported values: {supported}."
        )
    return provider


def get_llm_planner_provider() -> str:
    provider = str(
        _env_or_local_or_default(
            "LLM_PLANNER_PROVIDER",
            ("llm", "planner_provider"),
            DEFAULT_LLM_PLANNER_PROVIDER,
        )
    ).strip().lower()
    if provider not in SUPPORTED_LLM_PLANNER_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_LLM_PLANNER_PROVIDERS))
        raise ValueError(
            f"Unsupported LLM_PLANNER_PROVIDER: {provider}. "
            f"Supported values: {supported}."
        )
    return provider


def get_llm_planner_provider_source() -> str:
    if os.getenv("LLM_PLANNER_PROVIDER") is not None:
        return "environment"
    if _local_config_value("llm", "planner_provider") is not None:
        return "local_yaml"
    return "default"


def get_qwen_enabled() -> bool:
    value = str(
        _env_or_local_or_default(
            "QWEN_ENABLED",
            ("qwen", "enabled"),
            "false",
        )
    ).strip().lower()
    return value in QWEN_ENABLED_VALUES


def get_qwen_base_url() -> str:
    return str(
        _env_or_local_or_default(
            "QWEN_BASE_URL",
            ("qwen", "base_url"),
            DEFAULT_QWEN_BASE_URL,
        )
    ).strip()


def get_qwen_model() -> str:
    return str(
        _env_or_local_or_default(
            "QWEN_MODEL",
            ("qwen", "model"),
            DEFAULT_QWEN_MODEL,
        )
    ).strip()


def get_qwen_api_key() -> str:
    return str(
        _env_or_local_or_default(
            "QWEN_API_KEY",
            ("qwen", "api_key"),
            DEFAULT_QWEN_API_KEY,
        )
    ).strip()


def get_qwen_api_key_configured() -> bool:
    return bool(get_qwen_api_key())


def get_qwen_api_key_preview() -> str | None:
    api_key = get_qwen_api_key()
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}****{api_key[-4:]}"


def get_qwen_timeout_seconds() -> float:
    raw_timeout = str(
        _env_or_local_or_default(
            "QWEN_TIMEOUT_SECONDS",
            ("qwen", "timeout_seconds"),
            DEFAULT_QWEN_TIMEOUT_SECONDS,
        )
    ).strip()
    try:
        return float(raw_timeout)
    except ValueError as exc:
        raise ValueError(
            f"Invalid QWEN_TIMEOUT_SECONDS: {raw_timeout}. "
            "Expected a numeric timeout in seconds."
        ) from exc


def get_qwen_temperature() -> float:
    raw_temperature = str(
        _env_or_local_or_default(
            "QWEN_TEMPERATURE",
            ("qwen", "temperature"),
            DEFAULT_QWEN_TEMPERATURE,
        )
    ).strip()
    try:
        return float(raw_temperature)
    except ValueError as exc:
        raise ValueError(
            f"Invalid QWEN_TEMPERATURE: {raw_temperature}. "
            "Expected a numeric temperature."
        ) from exc


def get_qwen_enable_thinking() -> bool:
    value = _env_or_local_or_default(
        "QWEN_ENABLE_THINKING",
        ("qwen", "enable_thinking"),
        DEFAULT_QWEN_ENABLE_THINKING,
    )
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in QWEN_ENABLED_VALUES


def get_qwen_max_tokens() -> int:
    raw_max_tokens = str(
        _env_or_local_or_default(
            "QWEN_MAX_TOKENS",
            ("qwen", "max_tokens"),
            DEFAULT_QWEN_MAX_TOKENS,
        )
    ).strip()
    try:
        return int(raw_max_tokens)
    except ValueError as exc:
        raise ValueError(
            f"Invalid QWEN_MAX_TOKENS: {raw_max_tokens}. "
            "Expected an integer token limit."
        ) from exc


def get_trace_store_provider() -> str:
    provider = os.getenv("TRACE_STORE_PROVIDER", DEFAULT_TRACE_STORE_PROVIDER).strip().lower()
    if provider not in SUPPORTED_TRACE_STORE_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_TRACE_STORE_PROVIDERS))
        raise ValueError(
            f"Unsupported TRACE_STORE_PROVIDER: {provider}. Supported values: {supported}."
        )
    return provider


def get_trace_sqlite_path() -> str:
    return os.getenv("TRACE_SQLITE_PATH", DEFAULT_TRACE_SQLITE_PATH).strip()


def _env_or_local_or_default(env_name: str, keys: tuple[str, ...], default: Any) -> Any:
    env_value = os.getenv(env_name)
    if env_value is not None:
        return env_value

    local_value = _local_config_value(*keys)
    if local_value is not None:
        return local_value

    return default


def _local_config_value(*keys: str) -> Any:
    value: Any = _load_local_llm_config()
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _load_local_llm_config() -> dict[str, Any]:
    if _is_pytest_running() or not LOCAL_LLM_CONFIG_PATH.exists():
        return {}

    with LOCAL_LLM_CONFIG_PATH.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    return data if isinstance(data, dict) else {}


def _is_pytest_running() -> bool:
    return (
        "PYTEST_CURRENT_TEST" in os.environ
        or "PYTEST_VERSION" in os.environ
        or "pytest" in sys.modules
    )
