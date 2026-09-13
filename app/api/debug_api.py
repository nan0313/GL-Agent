from pathlib import Path

from fastapi import APIRouter

from app.anchors import AnchorRegistry
from app.config import (
    get_llm_planner_provider,
    get_llm_planner_provider_source,
    get_qwen_api_key_configured,
    get_qwen_api_key_preview,
    get_qwen_base_url,
    get_qwen_enabled,
    get_qwen_model,
    get_qwen_temperature,
    get_qwen_timeout_seconds,
    get_trace_store_provider,
    get_workflow_provider,
)
from app.rag import get_default_index_manager, get_default_index_store
from app.schemas.anchor import AnchorDefinition
from app.skills.registry import SkillRegistry
from app.integrations.webgl import get_webgl_contract
from app.qwen_gateway import get_qwen_gateway


router = APIRouter()
anchors_router = APIRouter()


@anchors_router.get("/anchors", response_model=list[AnchorDefinition])
def list_anchors() -> list[AnchorDefinition]:
    registry = AnchorRegistry()
    return registry.list_anchors()


@router.get("/debug/config")
def get_debug_config() -> dict[str, object]:
    return {
        "workflow_provider": get_workflow_provider(),
        "llm_planner_provider": get_llm_planner_provider(),
        "qwen_enabled": get_qwen_enabled(),
        "model_endpoint_configured": bool(get_qwen_base_url()),
        "qwen_model": get_qwen_model(),
        "qwen_timeout_seconds": get_qwen_timeout_seconds(),
        "qwen_temperature": get_qwen_temperature(),
        "qwen_api_key_configured": get_qwen_api_key_configured(),
        "qwen_api_key_preview": get_qwen_api_key_preview(),
        "config_source": get_llm_planner_provider_source(),
    }


@router.get("/debug/qwen_ping")
def qwen_ping() -> dict[str, object]:
    return {
        "status": "ok",
        "network_call": False,
        "message": "Qwen debug route is registered. No external model request was sent.",
        "llm_planner_provider": get_llm_planner_provider(),
        "qwen_enabled": get_qwen_enabled(),
        "qwen_model": get_qwen_model(),
        "model_endpoint_configured": bool(get_qwen_base_url()),
        "qwen_api_key_configured": get_qwen_api_key_configured(),
        "qwen_api_key_preview": get_qwen_api_key_preview(),
    }


@router.get("/health/qwen")
def qwen_health() -> dict[str, object]:
    return get_qwen_gateway().health_check().model_dump(exclude_none=True)


@router.get("/debug/routes")
def get_debug_routes() -> dict[str, object]:
    rag_stats = _get_rag_stats()
    try:
        skills = _list_skill_debug_items()
    except Exception:
        skills = []
    return {
        "workflow_provider": get_workflow_provider(),
        "llm_planner_provider": get_llm_planner_provider(),
        "qwen_enabled": get_qwen_enabled(),
        "qwen_model": get_qwen_model(),
        "model_endpoint_configured": bool(get_qwen_base_url()),
        "qwen_api_key_configured": get_qwen_api_key_configured(),
        "qwen_api_key_preview": get_qwen_api_key_preview(),
        "trace_store_provider": get_trace_store_provider(),
        "rag_enabled": True,
        "rag_provider": "local_rag_v2",
        "rag_pipeline_enabled": True,
        "rag_index_status": rag_stats.get("index_status"),
        "rag_index_version": rag_stats.get("index_version"),
        "knowledge_docs_count": rag_stats.get("docs_count", 0),
        "knowledge_chunks_count": rag_stats.get("chunks_count", 0),
        "knowledge_source_status_counts": rag_stats.get("source_status_counts", {}),
        "rag_lexical_available": rag_stats.get("fts5_available", False),
        "rag_dense_available": rag_stats.get("dense_available", False),
        "rag_embedding_model": rag_stats.get("embedding_model"),
        "rag_last_job": rag_stats.get("last_job"),
        "knowledge_error": "RAG_INDEX_UNAVAILABLE" if rag_stats.get("index_status") == "failed" else None,
        "skills_count": len(skills),
        "registered_skills": [item["skill_id"] for item in skills],
        "webgl_contract_path": "/debug/webgl-contract",
        "webgl_demo_path": "/webgl_bridge_demo.html",
        "webgl_bridge_js_path": "/webgl_agent_bridge.js",
        "webgl_ev_adapter_js_path": "/webgl_ev_cesium_adapter.js",
        "webgl_bootstrap_js_path": "/webgl_agent_bootstrap.js",
        "webgl_selected_object_bridge_js_path": "/webgl_selected_object_bridge.js",
        "webgl_ev_pick_patch_js_path": "/webgl_ev_pick_patch.js",
        "webgl_real_example_path": "/webgl_real_integration_example.html",
        "webgl_demo_available": _frontend_file_exists("webgl_bridge_demo.html"),
        "webgl_bridge_js_available": _frontend_file_exists("webgl_agent_bridge.js"),
        "webgl_ev_adapter_js_available": _frontend_file_exists("webgl_ev_cesium_adapter.js"),
        "webgl_bootstrap_js_available": _frontend_file_exists("webgl_agent_bootstrap.js"),
        "webgl_selected_object_bridge_js_available": _frontend_file_exists(
            "webgl_selected_object_bridge.js"
        ),
        "webgl_ev_pick_patch_js_available": _frontend_file_exists(
            "webgl_ev_pick_patch.js"
        ),
        "webgl_real_example_available": _frontend_file_exists(
            "webgl_real_integration_example.html"
        ),
    }


@router.get("/debug/rag")
def get_debug_rag() -> dict[str, object]:
    try:
        store = get_default_index_store()
        store.ensure_built()
        stats = store.get_stats()
        health = get_default_index_manager().health()
        documents = [
            {
                "doc_id": doc.doc_id,
                "title": doc.title,
                "source_name": Path(doc.source_path).name,
                "status": doc.status,
                "metadata": _safe_rag_metadata(doc.metadata),
            }
            for doc in store.list_documents()
        ]
        sample_chunks = [
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "title": chunk.title,
                "section": chunk.section,
                "source_name": Path(chunk.source_path).name,
                "char_count": chunk.char_count,
                "preview": chunk.content[:120],
                "metadata": _safe_rag_metadata(chunk.metadata),
            }
            for chunk in store.list_chunks()[:5]
        ]
        return {
            "ok": True,
            "provider": "local_rag_v2",
            "docs_count": stats["docs_count"],
            "chunks_count": stats["chunks_count"],
            "index_status": stats["index_status"],
            "documents": documents,
            "sample_chunks": sample_chunks,
            "health": {
                key: health.get(key)
                for key in (
                    "configured",
                    "available",
                    "lexical_available",
                    "dense_available",
                    "dense_status",
                    "reranker_available",
                    "active_index_version",
                    "source_count",
                    "document_count",
                    "chunk_count",
                    "last_build_status",
                    "last_build_at",
                    "embedding_model",
                    "vector_backend",
                    "configured_vector_backend",
                    "active_vector_backend",
                    "vector_backend_available",
                    "vector_item_count",
                    "vector_capacity",
                    "vector_deleted_count",
                    "vector_index_version",
                    "vector_fallback_used",
                    "vector_error_code",
                    "error_code",
                )
            },
            "error": "RAG_INDEX_UNAVAILABLE" if stats.get("index_status") == "failed" else None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "local_rag_v2",
            "docs_count": 0,
            "chunks_count": 0,
            "index_status": "failed",
            "documents": [],
            "sample_chunks": [],
            "error": "RAG_DEBUG_UNAVAILABLE",
        }


@router.get("/debug/skills")
def get_debug_skills() -> dict[str, object]:
    try:
        skills = _list_skill_debug_items()
        return {"status": "ok", "skills_count": len(skills), "skills": skills}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)[:200], "skills_count": 0, "skills": []}


@router.get("/debug/webgl-contract")
def get_debug_webgl_contract() -> dict[str, object]:
    return get_webgl_contract()


def _list_skill_debug_items() -> list[dict[str, object]]:
    registry = SkillRegistry.default()
    return [
        {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "description": skill.description,
            "route_type": skill.route_type,
            "enabled": skill.enabled,
            "risk_level": skill.risk_level,
            "timeout_seconds": skill.timeout_seconds,
            "required_roles": skill.required_roles,
            "class_name": skill.__class__.__name__,
        }
        for skill in registry.list_skills()
    ]


def _get_rag_stats() -> dict[str, object]:
    try:
        store = get_default_index_store()
        store.ensure_built()
        return store.get_stats()
    except Exception as exc:
        return {
            "provider": "local_rag_v2",
            "docs_count": 0,
            "chunks_count": 0,
            "index_status": "failed",
            "error": "RAG_INDEX_UNAVAILABLE",
        }


def _safe_rag_metadata(metadata: dict[str, object]) -> dict[str, object]:
    allowed = {"loader", "suffix", "char_count", "section_level", "chunk_index", "splitter", "max_chars", "overlap_chars"}
    return {key: value for key, value in metadata.items() if key in allowed and isinstance(value, (str, int, float, bool, type(None)))}


def _frontend_file_exists(filename: str) -> bool:
    return (Path(__file__).resolve().parents[2] / "frontend" / filename).exists()
