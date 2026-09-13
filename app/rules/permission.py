from typing import Any

from app.schemas.anchor import AnchorDefinition


class PermissionChecker:
    def check(self, role: str, anchor: AnchorDefinition) -> dict[str, Any]:
        if not anchor.permission_roles or role in anchor.permission_roles:
            return {
                "allowed": True,
                "reason": "allowed",
            }

        return {
            "allowed": False,
            "reason": "permission_denied",
            "message": f"当前角色无权调用锚点: {anchor.anchor_id}",
        }
