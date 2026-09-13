from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RouteType = Literal[
    "anchor_task",
    "general_chat",
    "rag_qa",
    "document_task",
    "unknown",
]


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="allow")

    route_type: RouteType
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    need_tool: bool = False
    need_rag: bool = False
    rewritten_query: str | None = None
    router: str = "rule"
    skill_name: str | None = None
    route_name: str | None = None
    source: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_webgl_route(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        if data.get("route_type") != "webgl_deterministic_anchor":
            return data

        normalized = dict(data)
        normalized["route_type"] = "anchor_task"
        normalized.setdefault("skill_name", "deterministic_webgl_anchor")
        normalized.setdefault("route_name", "webgl_deterministic_anchor")
        normalized.setdefault("source", "deterministic_router")
        normalized.setdefault("router", "deterministic_webgl")
        normalized.setdefault("need_tool", False)
        normalized.setdefault("need_rag", False)
        return normalized
