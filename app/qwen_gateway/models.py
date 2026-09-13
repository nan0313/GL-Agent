from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class QwenGatewayError(RuntimeError):
    """Safe, stable error returned by the shared gateway."""

    def __init__(self, code: str, message: str, *, purpose: str = "unknown", retryable: bool = False, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.purpose = purpose
        self.retryable = retryable
        self.status_code = status_code


class QwenHealth(BaseModel):
    configured: bool
    available: bool
    circuit_state: str
    last_status: str | None = None
    last_latency_ms: int = 0
    timeout_rate: float = 0.0
    last_checked_at: str | None = None
    error_code: str | None = None
    model: str | None = None
    model_endpoint_configured: bool = False


class QwenInvocation(BaseModel):
    request_id: str = ""
    invocation_id: str = Field(default_factory=lambda: f"qwen_{uuid4().hex}")
    purpose: str
    model: str
    status: str
    latency_ms: int = 0
    connect_ms: int | None = None
    first_byte_ms: int | None = None
    retry_count: int = 0
    circuit_state: str = "closed"
    input_tokens: int | None = None
    output_tokens: int | None = None
    prompt_chars: int = 0
    prompt_budget: dict[str, Any] = Field(default_factory=dict)
    fallback_used: bool = False
    error_code: str | None = None
    invocation_source: str = "real"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class TimeoutBudget:
    connect_timeout: float
    read_timeout: float
    write_timeout: float
    pool_timeout: float
    overall_deadline: float


@dataclass
class PromptBudgetResult:
    messages: list[dict[str, str]]
    original_chars: int
    final_chars: int
    removed_sections: list[str] = field(default_factory=list)
    budget_reason: str | None = None
