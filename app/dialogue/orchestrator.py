import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config import get_qwen_model
from app.conversations.service import resolve_conversation_id
from app.dialogue.grounding import GroundingValidator
from app.dialogue.ledger import ModelParticipation, default_model_ledger
from app.dialogue.models import DialogueState, DocumentContext, PendingClarification
from app.dialogue.normalizer import ChineseTextNormalizer, NormalizedText
from app.dialogue.resolver import ReferenceResolution, ReferenceResolver
from app.dialogue.semantic import GroundingResult, HybridRouteAudit, SemanticPlan, SemanticTarget
from app.dialogue.store import DialogueStateStore, default_dialogue_store
from app.llm.qwen_semantic_planner import QwenSemanticPlanner
from app.schemas.request import AgentChatRequest


class SemanticTurn(BaseModel):
    request_id: str
    normalization: NormalizedText
    state: DialogueState
    resolutions: list[ReferenceResolution] = Field(default_factory=list)
    route_audit: HybridRouteAudit
    plan: SemanticPlan | None = None
    grounding: GroundingResult | None = None
    clarification: PendingClarification | None = None
    rewritten_query: str
    model_participation: dict[str, Any] = Field(default_factory=dict)


class DialogueOrchestrator:
    def __init__(self, store: DialogueStateStore | None = None, semantic_planner: QwenSemanticPlanner | None = None) -> None:
        self.store = store or default_dialogue_store
        self.normalizer = ChineseTextNormalizer()
        self.resolver = ReferenceResolver()
        self.semantic_planner = semantic_planner or QwenSemanticPlanner()
        self.grounding = GroundingValidator()

    def prepare(self, request: AgentChatRequest) -> SemanticTurn:
        request_id = str((request.model_extra or {}).get("request_id") or f"req_{uuid4().hex}")
        conversation_id = resolve_conversation_id(request)
        state = self.store.begin_turn(conversation_id, request.gis_context)
        self._sync_document_context(state, request.user_id)
        normalized = self.normalizer.normalize(request.query)
        document_turn = self._uses_document_focus(normalized.normalized_text, state)
        if document_turn and state.pending_clarification is not None:
            # A GIS clarification from an earlier turn must not capture a
            # document follow-up merely because both use the pronoun “它”.
            state.pending_clarification.status = "superseded"
            state.clarification_history.append(state.pending_clarification.model_dump(exclude_none=True))
            state.pending_clarification = None
        resumed_plan, resumed_query = (None, None) if document_turn else self._resume_pending(state, normalized)
        candidates = ["document_task"] if document_turn else self._rule_candidates(normalized.normalized_text)
        candidate_intent = candidates[0] if len(candidates) == 1 else None
        resolutions = [] if document_turn else self.resolver.resolve_all(normalized.normalized_text, state, candidate_intent)
        complexity = [] if document_turn else self._complexity(normalized, resolutions)
        audit = HybridRouteAudit(route_source="hybrid" if complexity else "rule", rule_candidates=candidates, semantic_complexity=complexity, model_required=bool(complexity), final_intent=candidates[0] if len(candidates) == 1 else None, confidence=.95 if len(candidates) == 1 else .5)
        rewritten = resumed_query or self._rewrite(normalized.normalized_text, resolutions, state)
        plan = resumed_plan or self._rule_plan(rewritten, normalized, resolutions, state)
        planner_status, model_invoked, stages, latency, repaired, fallback = "not_invoked", False, [], {}, False, None
        if audit.model_required and resumed_plan is None:
            started = perf_counter()
            model_invoked = self.semantic_planner.available
            try:
                model_plan = self.semantic_planner.plan({"request_id": request.session_id, "original_text": request.query, "normalized_text": normalized.normalized_text, "quantities": [q.model_dump() for q in normalized.quantities], "reference_expressions": [r.expression for r in resolutions], "rule_candidates": candidates, "dialogue_summary": self._dialogue_summary(state)})
                planner_status, stages = "success", ["semantic_planner"]
                repaired = bool(self.semantic_planner.last_debug_info.get("repair_attempted"))
                if repaired: stages.append("schema_repair")
                plan = self._merge_model_plan(model_plan, plan, resolutions)
                audit.route_source = "qwen" if not candidates else "hybrid"
            except Exception as exc:
                planner_status = "fallback"
                fallback = self._safe_fallback(exc)
                audit.fallback_reason = fallback
            latency["semantic_planner"] = max(0, int((perf_counter() - started) * 1000))
        clarification = self._clarification_for(plan, resolutions, state, request.query, normalized.normalized_text)
        if resumed_plan is not None:
            clarification = None
            state.pending_clarification = None
        grounding = self.grounding.validate(plan, state) if plan else None
        participation = ModelParticipation(request_id=request_id, route_source=audit.route_source, model_invoked=model_invoked, model_name=get_qwen_model() if model_invoked else None, model_stages=stages, latency_ms=latency, rule_candidates=candidates, normalized_text=normalized.normalized_text, resolved_references=[r.model_dump(exclude_none=True) for r in resolutions], planner_status=planner_status, planner_repaired=repaired, fallback_reason=fallback)
        default_model_ledger.add(request.session_id, participation)
        state.recent_turns.append({"turn_index": state.turn_index, "original_text": request.query, "normalized_text": normalized.normalized_text, "route_source": audit.route_source})
        state.recent_turns = state.recent_turns[-20:]
        if clarification:
            state.pending_clarification = clarification
        self.store.save(state)
        return SemanticTurn(request_id=request_id, normalization=normalized, state=state, resolutions=resolutions, route_audit=audit, plan=plan, grounding=grounding, clarification=clarification, rewritten_query=rewritten, model_participation=participation.model_dump(exclude_none=True))

    @staticmethod
    def _uses_document_focus(text: str, state: DialogueState) -> bool:
        """Choose attachment focus before the GIS pronoun resolver.

        Document and GIS focuses intentionally coexist.  Only document-shaped
        language binds “它/这个/里面” to an attachment; explicit map actions
        continue to use the GIS focus.
        """
        normalized = re.sub(r"[\s，。！？?!,.]", "", str(text or "")).casefold()
        active_ids = set(state.document_context.active_attachment_ids)
        selected_ids = set(state.document_context.selected_attachment_ids) & active_ids
        if not active_ids or not (selected_ids or len(active_ids) == 1):
            return False
        if any(term in normalized for term in ("飞到", "定位", "高亮", "缓冲", "地图", "图层", "视角", "对象属性")):
            return False
        if any(term in normalized for term in ("文件", "附件", "文档", "pdf", "word", "总结", "概括", "提炼")):
            return True
        pronoun = bool(re.match(r"^(?:它|里面|其中|这个|该|上述|刚才)(?:的|里面|中)?", normalized))
        question = any(term in normalized for term in (
            "多少", "是什么", "哪些", "哪几", "何时", "什么时候", "是否", "有没有",
            "在哪", "怎么", "如何", "为什么", "包括", "包含", "要求", "规定", "分别",
        ))
        return pronoun and question

    @staticmethod
    def _resume_pending(state: DialogueState, normalized: NormalizedText) -> tuple[SemanticPlan | None, str | None]:
        pending = state.pending_clarification
        if not pending:
            return None, None
        answer = normalized.normalized_text
        if state.turn_index - pending.created_turn > pending.expires_after_turns:
            pending.status = "expired"
            state.clarification_history.append(pending.model_dump(exclude_none=True))
            state.pending_clarification = None
            return None, None
        if any(x in answer for x in ["取消", "算了", "不用了"]):
            pending.status = "canceled"
            pending.resolved_at = __import__("app.dialogue.models", fromlist=["utc_now"]).utc_now()
            state.clarification_history.append(pending.model_dump(exclude_none=True))
            state.pending_clarification = None
            return SemanticPlan(intent="unknown", response_intent="cancel", confidence=1), answer
        explicit_new = any(x in answer for x in ["查询", "高亮", "飞到", "清除", "查看", "缓冲"])
        if explicit_new or len(answer) > 40:
            state.pending_clarification = None
            return None, None
        try:
            plan = SemanticPlan.model_validate(pending.plan)
        except Exception:
            state.pending_clarification = None
            return None, None
        if pending.missing_field == "target" and answer.strip():
            plan.target = SemanticTarget(explicit_name=answer.strip("。！？? "))
            plan.needs_clarification = False
            distance = plan.parameters.get("distance_m")
            rewritten = f"给{plan.target.explicit_name}做{float(distance):g}米缓冲区" if plan.intent == "create_buffer" and distance else pending.normalized_utterance.replace("它", plan.target.explicit_name)
            pending.status = "resolved"
            pending.resolved_at = __import__("app.dialogue.models", fromlist=["utc_now"]).utc_now()
            state.clarification_history.append(pending.model_dump(exclude_none=True))
            return plan, rewritten
        if pending.missing_field == "distance_m":
            quantity = next((q for q in normalized.quantities if q.dimension == "length"), None)
            if quantity is not None:
                plan.parameters["distance_m"] = quantity.value
                plan.needs_clarification = False
                target = plan.target.explicit_name if plan.target else None
                rewritten = f"给{target}做{quantity.value:g}米缓冲区" if target else pending.normalized_utterance
                pending.status = "resolved"
                pending.resolved_at = __import__("app.dialogue.models", fromlist=["utc_now"]).utc_now()
                state.clarification_history.append(pending.model_dump(exclude_none=True))
                return plan, rewritten
        return None, None

    @staticmethod
    def _complexity(normalized: NormalizedText, refs: list[ReferenceResolution]) -> list[str]:
        text = normalized.original_text
        out: list[str] = []
        # A uniquely resolved, action-compatible reference is deterministic.
        # Only unresolved/ambiguous reference language requires model arbitration.
        if any(not ref.resolved or ref.needs_clarification for ref in refs):
            out.append("reference_ambiguity")
        # A locally normalized quantity is evidence for the deterministic route,
        # not a reason to invoke the model by itself.
        if any(x in text for x in ["然后", "再查询", "再查", "并且", "再给"]): out.append("multi_step")
        if any(x in text for x in ["再扩大", "改成", "缩小到", "再加", "还是改"]): out.append("modify_previous_result")
        if any(r.needs_clarification for r in refs): out.append("ambiguity")
        return list(dict.fromkeys(out))

    @staticmethod
    def _rule_candidates(text: str) -> list[str]:
        candidates = []
        if "清除所有缓冲区" in text:
            return ["clear_all_buffers"]
        if "清除缓冲区" in text:
            return ["clear_buffer"]
        checks = [("create_buffer", ["缓冲"]), ("query_objects_in_buffer", ["查询", "里面"]), ("gis_highlight", ["高亮"]), ("clear_highlight", ["清除高亮", "取消高亮"]), ("get_object_properties", ["属性"]), ("locate_admin_region", ["飞到", "定位"])]
        for intent, words in checks:
            if all(w in text for w in words): candidates.append(intent)
        if "缓冲" in text and "查询" in text: return ["multi_step"]
        return candidates

    def _rewrite(self, text: str, refs: list[ReferenceResolution], state: DialogueState) -> str:
        rewritten = text
        if any(x in text for x in ["清除高亮", "取消高亮"]) and ("保留缓冲" in text or any(r.kind == "object_set" for r in refs)):
            rewritten = "清除缓冲区查询结果高亮"
        for resolution in refs:
            if not resolution.resolved or not resolution.reference_ids: continue
            if resolution.kind == "object_set" and "高亮" in rewritten and "清除" not in rewritten and "取消" not in rewritten:
                rewritten = "高亮缓冲区内对象"
                continue
            focus = next((f for f in state.focus_stack if f.reference_id == resolution.reference_ids[0] and f.active), None)
            label = focus.label if focus else None
            if not label and state.last_object_set:
                member = next((item for item in state.last_object_set.items if item.reference_id == resolution.reference_ids[0]), None)
                label = member.name if member else None
            if label and resolution.kind in {"object", "admin_region"}:
                rewritten = rewritten.replace(resolution.expression, label)
        if "缓冲" in rewritten and any(x in rewritten for x in ["然后", "再查询", "再查"]):
            admin = re.search(r"(?:找到|飞到|定位)([^，,。；;]+?(?:省|市|自治区|区|县))", rewritten)
            distance = next((q.value for q in self.normalizer.normalize(rewritten).quantities if q.dimension == "length"), None)
            if admin and distance:
                object_type = "站点" if "站点" in rewritten else "对象"
                rewritten = f"找到{admin.group(1)}，然后给{admin.group(1)}做{distance:g}米缓冲区并查询其中的{object_type}"
        if any(x in text for x in ["再扩大一倍", "扩大一倍"]):
            old = float((state.last_buffer or {}).get("distance_m") or 0)
            target = (state.last_buffer or {}).get("target") or {}
            name = target.get("name") or target.get("object_name") or (state.last_admin_region or {}).get("name")
            if old and name: rewritten = f"给{name}做{old * 2:g}米缓冲区"
        elif "缩小到原来的一半" in text or "缩小到一半" in text:
            old = float((state.last_buffer or {}).get("distance_m") or 0); target = (state.last_buffer or {}).get("target") or {}; name = target.get("name") or target.get("object_name")
            if old and name: rewritten = f"给{name}做{old / 2:g}米缓冲区"
        elif any(x in text for x in ["改成", "还是改成"] ) and state.last_buffer and self.normalizer.normalize(text).quantities:
            value = self.normalizer.normalize(text).quantities[0].value; target = state.last_buffer.get("target") or {}; name = target.get("name") or target.get("object_name") or (state.last_admin_region or {}).get("name")
            if name: rewritten = f"给{name}做{value:g}米缓冲区"
        return rewritten

    def _rule_plan(self, text: str, normalized: NormalizedText, refs: list[ReferenceResolution], state: DialogueState) -> SemanticPlan | None:
        if "东北三省" in text:
            return SemanticPlan(intent="unknown", confidence=1.0, needs_clarification=True, clarification="COMPOSITE_REGION_REQUIRES_CLARIFICATION")
        if "查询湖北省中的对象" in text:
            return SemanticPlan(intent="unknown", confidence=1.0, needs_clarification=True, clarification="ADMIN_REGION_QUERY_CAPABILITY_LIMIT")
        if "清除所有缓冲区" in text:
            return SemanticPlan(intent="clear_all_buffers", confidence=1.0)
        if "清除缓冲区" in text:
            return SemanticPlan(intent="clear_buffer", confidence=1.0)
        if "查询" in text and any(x in text for x in ["里面", "其中", "缓冲区内", "缓冲区里", "缓冲区中"]):
            return SemanticPlan(intent="query_objects_in_buffer", parameters={"object_type": "substation" if "站点" in text else None}, confidence=.95)
        if "缓冲" in text and (normalized.quantities or any(word in text for word in ["生成", "创建", "建立", "做", "向外缓冲"])):
            distance = next((q.value for q in normalized.quantities if q.dimension == "length"), None)
            name_match = re.search(r"(?:给|以)(.+?)(?:边界)?(?:做|生成|创建|向外缓冲)", text)
            name = name_match.group(1) if name_match else None
            target = SemanticTarget(explicit_name=name)
            resolved = next((r for r in refs if r.resolved and len(r.reference_ids) == 1), None)
            if resolved and not name:
                target = SemanticTarget(reference_expression=resolved.expression, reference_kind=resolved.kind, resolved_reference_id=resolved.reference_ids[0])
            return SemanticPlan(intent="create_buffer", target=target, parameters={"distance_m": distance} if distance else {}, confidence=.95, needs_clarification=not bool(name or resolved))
        if "高亮" in text and any(r.kind == "object_set" for r in refs): return SemanticPlan(intent="highlight_buffer_query_results", confidence=.98)
        return None

    @staticmethod
    def _merge_model_plan(model: SemanticPlan, rule: SemanticPlan | None, refs: list[ReferenceResolution]) -> SemanticPlan:
        plan = model
        if plan.target and plan.target.reference_expression:
            resolved = next((r for r in refs if r.expression == plan.target.reference_expression and r.resolved and len(r.reference_ids) == 1), None)
            if resolved: plan.target.resolved_reference_id = resolved.reference_ids[0]
        if rule and not plan.parameters: plan.parameters = rule.parameters
        return plan

    @staticmethod
    def _clarification_for(plan: SemanticPlan | None, refs: list[ReferenceResolution], state: DialogueState, original: str, normalized: str) -> PendingClarification | None:
        if plan and plan.intent == "create_buffer" and plan.parameters.get("distance_m") is None:
            return PendingClarification(missing_field="distance_m", missing_fields=["distance_m"], question="请提供缓冲距离，例如500米或1公里。", original_utterance=original, normalized_utterance=normalized, plan=plan.model_dump(exclude_none=True), created_turn=state.turn_index, expires_turn=state.turn_index + 3)
        if plan and plan.clarification == "COMPOSITE_REGION_REQUIRES_CLARIFICATION":
            return PendingClarification(missing_field="composite_region_strategy", missing_fields=["composite_region_strategy"], question="你希望分别为黑龙江省、吉林省和辽宁省生成三个缓冲区，还是先合并三个省的边界后生成一个缓冲区？", original_utterance=original, normalized_utterance=normalized, plan=plan.model_dump(exclude_none=True), created_turn=state.turn_index, expires_turn=state.turn_index + 3)
        if plan and plan.clarification == "ADMIN_REGION_QUERY_CAPABILITY_LIMIT":
            return PendingClarification(missing_field="supported_capability", missing_fields=["supported_capability"], question="当前支持查询缓冲区内对象。请先为湖北省生成缓冲区，或明确需要新增行政区范围查询能力。", original_utterance=original, normalized_utterance=normalized, plan=plan.model_dump(exclude_none=True), created_turn=state.turn_index, expires_turn=state.turn_index + 3)
        unresolved = next((r for r in refs if r.needs_clarification), None)
        if unresolved or (plan and plan.needs_clarification):
            candidates = unresolved.candidates if unresolved else []
            question = "请说明要操作的具体对象。"
            if candidates: question = "你是指" + "，还是".join(f"{x.get('label') or x.get('reference_id')}（{x.get('type_label') or x.get('kind')}）" for x in candidates) + "？"
            elif unresolved and unresolved.reason: question = unresolved.reason + "，请先查询或选择对象。"
            return PendingClarification(missing_field="target", missing_fields=["target"], question=question, candidates=candidates, original_utterance=original, normalized_utterance=normalized, plan=plan.model_dump(exclude_none=True) if plan else {}, created_turn=state.turn_index, expires_turn=state.turn_index + 3)
        return None

    @staticmethod
    def _dialogue_summary(state: DialogueState) -> dict[str, Any]:
        return {
            "turn_index": state.turn_index,
            "active_focuses": [{"kind": f.kind, "label": f.label, "plural": f.plural} for f in state.active_focuses()[:8]],
            "has_pending_clarification": state.pending_clarification is not None,
            "document_context": {
                "attachment_count": state.document_context.attachment_count,
                "active_attachment_ids": state.document_context.active_attachment_ids,
                "selected_attachment_ids": state.document_context.selected_attachment_ids,
            },
        }

    def _sync_document_context(self, state: DialogueState, user_id: str) -> None:
        """Refresh attachment facts from the backend store on every turn."""
        try:
            from app.conversations.attachments import get_default_attachment_service

            snapshot = get_default_attachment_service().state_snapshot(state.conversation_id, user_id)
        except Exception:
            # Direct workflow tests may intentionally have no persistent
            # conversation. They keep their process-local dialogue behavior.
            return
        attachments = list(snapshot.get("attachments") or [])
        active_ids = list(snapshot.get("active_attachment_ids") or [])
        by_status = {str(item.get("attachment_id")): str(item.get("status")) for item in attachments}
        selected = [item for item in state.document_context.selected_attachment_ids if item in active_ids]
        removed = set(state.document_context.selected_attachment_ids) - set(selected)
        for attachment_id in removed:
            state.invalidate_reference("attachment", attachment_id)
        state.document_context = DocumentContext(
            last_attachment_id=snapshot.get("last_attachment_id"),
            attachment_count=int(snapshot.get("attachment_count") or 0),
            active_attachment_ids=active_ids,
            indexing_attachment_ids=[key for key, status in by_status.items() if status in {"UPLOADED", "PARSING", "INDEXING"}],
            failed_attachment_ids=[key for key, status in by_status.items() if status == "FAILED"],
            ocr_required_attachment_ids=[key for key, status in by_status.items() if status == "OCR_REQUIRED"],
            selected_attachment_ids=selected,
            attachments=attachments,
            last_document_intent=state.document_context.last_document_intent,
            last_document_query=state.document_context.last_document_query,
            last_evidence_chunk_ids=state.document_context.last_evidence_chunk_ids,
        )

    @staticmethod
    def _safe_fallback(exc: Exception) -> str:
        gateway_error = getattr(exc, "gateway_error", None)
        if gateway_error is not None:
            return "模型服务暂时不可用，已降级到本地规则或澄清。"
        text = str(exc)
        if any(x.lower() in text.lower() for x in ["api", "key", "authorization", "bearer", "token", "base_url", "http://", "https://", "endpoint"]):
            return "模型服务暂时不可用，已降级到本地规则或澄清。"
        return "模型语义规划暂时不可用，已降级到本地规则或澄清。"
