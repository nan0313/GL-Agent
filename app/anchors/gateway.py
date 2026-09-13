from uuid import uuid4

from app.anchors.mock_adapter import MockAnchorAdapter
from app.schemas.anchor import AnchorDefinition, AnchorResult
from app.schemas.response import ToolCall


class AnchorGateway:
    def __init__(self, mock_adapter: MockAnchorAdapter | None = None) -> None:
        self.mock_adapter = mock_adapter or MockAnchorAdapter()

    def execute(self, tool_call: ToolCall, anchor: AnchorDefinition) -> AnchorResult:
        anchor_call_id = f"anchor_call_{uuid4().hex}"

        if anchor.status == "offline":
            return self._failed_result(
                anchor_call_id=anchor_call_id,
                anchor=anchor,
                error=f"Anchor is offline: {anchor.anchor_id}",
            )

        if anchor.status == "developing":
            return AnchorResult(
                anchor_call_id=anchor_call_id,
                anchor_id=anchor.anchor_id,
                status="skipped",
                data={},
                error=f"Anchor is still developing: {anchor.anchor_id}",
                ui_events=[],
                evidence=[],
            )

        if anchor.adapter_type in {"mock", "frontend_event"}:
            return self.mock_adapter.execute(
                tool_call=tool_call,
                anchor=anchor,
                anchor_call_id=anchor_call_id,
            )

        return self._failed_result(
            anchor_call_id=anchor_call_id,
            anchor=anchor,
            error=f"Adapter type is not implemented yet: {anchor.adapter_type}",
        )

    def _failed_result(
        self,
        anchor_call_id: str,
        anchor: AnchorDefinition,
        error: str,
    ) -> AnchorResult:
        return AnchorResult(
            anchor_call_id=anchor_call_id,
            anchor_id=anchor.anchor_id,
            status="failed",
            data={},
            error=error,
            ui_events=[],
            evidence=[],
        )
