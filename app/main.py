from pathlib import Path
import os

from app.release_profile import apply_release_profile, debug_routes_enabled, developer_mode_enabled, get_agent_mode, local_admin_mode_enabled


RUNTIME_PROFILE_STATUS = apply_release_profile()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.agent_api import agent_chat, get_trace
from app.api.agent_api import router as agent_router
from app.api.agent_api import developer_router as developer_agent_router
from app.api.agent_stream import agent_chat_stream_api, agent_chat_stream_compat
from app.api.agent_stream import router as agent_stream_router
from app.api.debug_api import anchors_router
from app.api.debug_api import (
    get_debug_config,
    get_debug_rag,
    get_debug_routes,
    get_debug_skills,
    get_debug_webgl_contract,
    list_anchors,
    qwen_ping,
    qwen_health,
)
from app.api.debug_api import router as debug_router
from app.api.admin_region_api import router as admin_region_router
from app.api.attachment_api import router as attachment_router
from app.api.artifact_api import router as artifact_router
from app.api.conversation_api import router as conversation_router
from app.api.knowledge_api import router as knowledge_router
from app.api.rag_api import router as rag_router
from app.schemas.anchor import AnchorDefinition
from app.schemas.request import AgentChatRequest
from app.schemas.response import AgentResponse
from app.schemas.trace import TraceRecord
from app.qwen_gateway import get_qwen_gateway
from app.rag import get_default_index_manager
from app.rag.index_store import close_default_index_manager
from app.conversations import close_default_conversation_store
from app.release_metadata import get_release_metadata
from app.release_profile import public_profile_status


app = FastAPI(
    title="EV Agent" if not developer_mode_enabled() else "EV Agent Developer API",
    description="EV Agent customer API." if not developer_mode_enabled() else "Developer API for EV Agent diagnostics.",
    version="0.1.0",
    docs_url="/docs" if developer_mode_enabled() else None,
    redoc_url="/redoc" if developer_mode_enabled() else None,
    openapi_url="/openapi.json" if developer_mode_enabled() else None,
)


@app.middleware("http")
async def disable_agent_frontend_cache(request, call_next):
    """Agent integration scripts evolve independently of the immutable WebGL bundle."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/frontend/") or path in {
        "/webgl_agent_bridge.js", "/webgl_ev_cesium_adapter.js",
        "/webgl_agent_bootstrap.js", "/ev_agent_stream_client.js",
        "/ev_agent_ui_event_queue.js", "/webgl_selected_object_bridge.js",
        "/webgl_ev_pick_patch.js", "/conversation_client.js", "/conversation_ui.js",
        "/attachment_client.js", "/knowledge_manager_ui.js",
    }:
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def _get_cors_allow_origins() -> list[str]:
    raw_origins = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    if raw_origins:
        return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    return [
        "http://127.0.0.1:8090",
        "http://localhost:8090",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)
app.include_router(agent_stream_router)
if debug_routes_enabled():
    app.include_router(developer_agent_router)
    app.include_router(anchors_router)
    app.include_router(debug_router)
app.include_router(admin_region_router)
app.include_router(conversation_router)
app.include_router(attachment_router)
app.include_router(artifact_router)
app.include_router(knowledge_router)
if local_admin_mode_enabled():
    app.include_router(rag_router)

@app.on_event("shutdown")
async def close_shared_qwen_clients() -> None:
    await get_qwen_gateway().aclose()
    close_default_index_manager()
    try:
        from app.conversations.attachments import close_default_attachment_service

        close_default_attachment_service()
    finally:
        close_default_conversation_store()


@app.on_event("startup")
async def initialize_shared_qwen_gateway() -> None:
    # Materialize the process-wide gateway once per application lifecycle.
    get_qwen_gateway()


def _direct_route_paths() -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def _ensure_top_level_routes_registered() -> None:
    paths = _direct_route_paths()
    if "/agent/chat" not in paths:
        app.add_api_route(
            "/agent/chat",
            agent_chat,
            methods=["POST"],
            response_model=AgentResponse,
        )
    if "/api/agent/chat/stream" not in paths:
        app.add_api_route(
            "/api/agent/chat/stream",
            agent_chat_stream_api,
            methods=["POST"],
        )
    if "/agent/chat/stream" not in paths:
        app.add_api_route(
            "/agent/chat/stream",
            agent_chat_stream_compat,
            methods=["POST"],
        )
    if debug_routes_enabled() and "/trace/{trace_id}" not in paths:
        app.add_api_route(
            "/trace/{trace_id}",
            get_trace,
            methods=["GET"],
            response_model=TraceRecord,
        )
    if debug_routes_enabled() and "/anchors" not in paths:
        app.add_api_route(
            "/anchors",
            list_anchors,
            methods=["GET"],
            response_model=list[AnchorDefinition],
        )
    if debug_routes_enabled() and "/debug/config" not in paths:
        app.add_api_route("/debug/config", get_debug_config, methods=["GET"])
    if debug_routes_enabled() and "/debug/qwen_ping" not in paths:
        app.add_api_route("/debug/qwen_ping", qwen_ping, methods=["GET"])
    if debug_routes_enabled() and "/health/qwen" not in paths:
        app.add_api_route("/health/qwen", qwen_health, methods=["GET"])
    if debug_routes_enabled() and "/debug/routes" not in paths:
        app.add_api_route("/debug/routes", get_debug_routes, methods=["GET"])
    if debug_routes_enabled() and "/debug/rag" not in paths:
        app.add_api_route("/debug/rag", get_debug_rag, methods=["GET"])
    if debug_routes_enabled() and "/debug/skills" not in paths:
        app.add_api_route("/debug/skills", get_debug_skills, methods=["GET"])
    if debug_routes_enabled() and "/debug/webgl-contract" not in paths:
        app.add_api_route("/debug/webgl-contract", get_debug_webgl_contract, methods=["GET"])


_ensure_top_level_routes_registered()

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


def get_registered_route_paths() -> list[str]:
    return sorted(getattr(route, "path", "") for route in app.routes)


def _frontend_path(filename: str) -> Path:
    return FRONTEND_DIR / filename


def _frontend_file_response(filename: str, media_type: str) -> FileResponse:
    file_path = _frontend_path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    return FileResponse(file_path, media_type=media_type)


def _frontend_file_response_with_fallback(filename: str, fallback: str, media_type: str) -> FileResponse:
    file_path = _frontend_path(filename)
    if not file_path.exists():
        file_path = _frontend_path(fallback)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} and {fallback} not found")
    return FileResponse(file_path, media_type=media_type)


@app.get("/", response_class=HTMLResponse)
def frontend_index() -> FileResponse:
    return _frontend_file_response_with_fallback("index.html", "mvp.html", "text/html")


@app.get("/webgl_bridge_demo.html", response_class=HTMLResponse)
def webgl_bridge_demo() -> FileResponse:
    return _frontend_file_response("webgl_bridge_demo.html", "text/html")


@app.get("/webgl_agent_bridge.js")
def webgl_agent_bridge_js() -> FileResponse:
    return _frontend_file_response("webgl_agent_bridge.js", "application/javascript")


@app.get("/webgl_ev_cesium_adapter.js")
def webgl_ev_cesium_adapter_js() -> FileResponse:
    return _frontend_file_response("webgl_ev_cesium_adapter.js", "application/javascript")


@app.get("/webgl_agent_bootstrap.js")
def webgl_agent_bootstrap_js() -> FileResponse:
    return _frontend_file_response("webgl_agent_bootstrap.js", "application/javascript")


@app.get("/ev_agent_stream_client.js")
def ev_agent_stream_client_js() -> FileResponse:
    return _frontend_file_response("ev_agent_stream_client.js", "application/javascript")


@app.get("/ev_agent_ui_event_queue.js")
def ev_agent_ui_event_queue_js() -> FileResponse:
    return _frontend_file_response("ev_agent_ui_event_queue.js", "application/javascript")


@app.get("/webgl_selected_object_bridge.js")
def webgl_selected_object_bridge_js() -> FileResponse:
    return _frontend_file_response("webgl_selected_object_bridge.js", "application/javascript")


@app.get("/webgl_ev_pick_patch.js")
def webgl_ev_pick_patch_js() -> FileResponse:
    return _frontend_file_response("webgl_ev_pick_patch.js", "application/javascript")


@app.get("/conversation_client.js")
def conversation_client_js() -> FileResponse:
    return _frontend_file_response("conversation_client.js", "application/javascript")


@app.get("/conversation_ui.js")
def conversation_ui_js() -> FileResponse:
    return _frontend_file_response("conversation_ui.js", "application/javascript")


@app.get("/attachment_client.js")
def attachment_client_js() -> FileResponse:
    return _frontend_file_response("attachment_client.js", "application/javascript")


@app.get("/knowledge_manager_ui.js")
def knowledge_manager_ui_js() -> FileResponse:
    return _frontend_file_response("knowledge_manager_ui.js", "application/javascript")


@app.get("/webgl_real_integration_example.html", response_class=HTMLResponse)
def webgl_real_integration_example() -> FileResponse:
    return _frontend_file_response("webgl_real_integration_example.html", "text/html")


@app.get("/index_agent.html", response_class=HTMLResponse)
def frontend_index_agent() -> FileResponse:
    return _frontend_file_response_with_fallback("index_agent.html", "mvp_index_agent.html", "text/html")


@app.get("/api/agent/health")
def agent_health() -> dict[str, object]:
    release = get_release_metadata()
    if not developer_mode_enabled():
        return {
            "status": "ok",
            "service": "ev-agent",
            "streaming": True,
            "agent_mode": get_agent_mode(),
            "public_event_contract": "customer.v1",
            "release_id": release.get("release_id"),
            "capabilities": {
                "persistent_conversations": True,
                "conversation_attachments": True,
                "citations": True,
                "business_actions": True,
            },
        }
    try:
        rag = get_default_index_manager().health()
        rag_status = {
            "source_count": rag.get("source_count", 0),
            "document_count": rag.get("document_count", 0),
            "chunk_count": rag.get("chunk_count", 0),
            "embedding_count": (rag.get("last_job") or {}).get("embedding_count", 0),
            "active_index_version": rag.get("active_index_version"),
            "active_vector_backend": rag.get("active_vector_backend"),
            "dense_available": rag.get("dense_available", False),
            "real_dense_validated": rag.get("real_dense_validated", False),
            "vector_valid": not bool(rag.get("vector_error_code")),
            "model_status": public_profile_status()["rag_model_status"],
        }
    except Exception:
        rag_status = {"status": "unavailable", "error_code": "RAG_HEALTH_UNAVAILABLE"}
    return {
        "status": "ok", "service": "ev-agent", "streaming": True, "agent_mode": get_agent_mode(),
        **release,
        "capabilities": {
            "persistent_conversations": True,
            "conversation_attachments": True,
            "dual_scope_rag": True,
            "global_knowledge_upload": True,
            "local_admin_mode": local_admin_mode_enabled(),
        },
        "rag": rag_status, "qwen": get_qwen_gateway().status_snapshot(),
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
