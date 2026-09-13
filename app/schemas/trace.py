from pydantic import BaseModel, ConfigDict, Field


class TraceStep(BaseModel):
    model_config = ConfigDict(extra="allow")

    step_name: str
    status: str
    input_summary: str
    output_summary: str
    error: str | None = None
    latency_ms: int


class TraceRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    trace_id: str
    session_id: str
    user_id: str
    query: str
    steps: list[TraceStep] = Field(default_factory=list)
    status: str
