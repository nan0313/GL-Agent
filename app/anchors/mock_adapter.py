from app.schemas.anchor import AnchorDefinition, AnchorResult
from app.schemas.response import EvidenceItem, ToolCall, UIEvent


class MockAnchorAdapter:
    def execute(
        self,
        tool_call: ToolCall,
        anchor: AnchorDefinition,
        anchor_call_id: str,
    ) -> AnchorResult:
        if anchor.anchor_id == "placeholder_search_object":
            return self._execute_search_object(tool_call, anchor, anchor_call_id)

        if anchor.anchor_id == "placeholder_open_panel":
            return self._execute_open_panel(tool_call, anchor, anchor_call_id)

        return AnchorResult(
            anchor_call_id=anchor_call_id,
            anchor_id=anchor.anchor_id,
            status="failed",
            data={},
            error=f"Mock adapter does not support anchor: {anchor.anchor_id}",
            ui_events=[],
            evidence=[],
        )

    def _execute_search_object(
        self,
        tool_call: ToolCall,
        anchor: AnchorDefinition,
        anchor_call_id: str,
    ) -> AnchorResult:
        object_id = str(tool_call.arguments.get("object_id") or "MOCK_OBJECT_001")
        object_type = str(tool_call.arguments.get("object_type") or "generic_object")
        object_name = str(
            tool_call.arguments.get("object_name")
            or tool_call.arguments.get("keyword")
            or "Mock Object"
        )
        lon = float(tool_call.arguments.get("lon") or 114.05)
        lat = float(tool_call.arguments.get("lat") or 22.55)
        data = {
            "object_id": object_id,
            "object_type": object_type,
            "object_name": object_name,
            "lon": lon,
            "lat": lat,
            "mock": True,
        }

        return AnchorResult(
            anchor_call_id=anchor_call_id,
            anchor_id=anchor.anchor_id,
            status="success",
            data=data,
            error=None,
            ui_events=[
                UIEvent(
                    type="fly_to",
                    target=object_id,
                    params={"lon": lon, "lat": lat, "zoom": 16},
                ),
                UIEvent(
                    type="gis_highlight",
                    target=object_id,
                    params={"object_id": object_id, "object_type": object_type},
                ),
            ],
            evidence=[self._success_evidence(anchor)],
        )

    def _execute_open_panel(
        self,
        tool_call: ToolCall,
        anchor: AnchorDefinition,
        anchor_call_id: str,
    ) -> AnchorResult:
        panel_id = str(tool_call.arguments.get("panel_id") or anchor.anchor_id)
        object_id = tool_call.arguments.get("object_id")
        object_type = tool_call.arguments.get("object_type")
        data = {
            "panel_id": panel_id,
            "object_id": object_id,
            "object_type": object_type,
            "mock": True,
        }
        ui_events = [
            UIEvent(
                type="open_panel",
                target=panel_id,
                params=dict(tool_call.arguments),
            )
        ]

        if object_id:
            ui_events.append(
                UIEvent(
                    type="gis_highlight",
                    target=str(object_id),
                    params={"object_id": object_id, "object_type": object_type},
                )
            )

        return AnchorResult(
            anchor_call_id=anchor_call_id,
            anchor_id=anchor.anchor_id,
            status="success",
            data=data,
            error=None,
            ui_events=ui_events,
            evidence=[self._success_evidence(anchor)],
        )

    def _success_evidence(self, anchor: AnchorDefinition) -> EvidenceItem:
        return EvidenceItem(
            anchor_id=anchor.anchor_id,
            anchor_name=anchor.anchor_name,
            status="success",
            data_time="mock_time",
            summary=f"Mock 执行成功：{anchor.anchor_name}",
        )
