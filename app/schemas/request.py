from pydantic import BaseModel, ConfigDict, Field


class SelectedObject(BaseModel):
    model_config = ConfigDict(extra="allow")

    object_type: str | None = None
    object_id: str
    object_name: str | None = None


class GISContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    selected_object: SelectedObject | None = None
    active_layers: list[str] = Field(default_factory=list)
    map_center: tuple[float, float] | None = None
    zoom: int | float | None = None


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str | None = None
    conversation_id: str | None = None
    request_id: str | None = None
    user_id: str
    role: str
    query: str
    gis_context: GISContext
