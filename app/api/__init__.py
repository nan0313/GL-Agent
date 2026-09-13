"""API route modules."""

from app.api.agent_api import router as agent_router
from app.api.debug_api import anchors_router
from app.api.debug_api import router as debug_router

__all__ = ["agent_router", "anchors_router", "debug_router"]
