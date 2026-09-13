from time import perf_counter
from typing import Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, StateGraph

from app.agent.finalizer import build_final_response
from app.answering import attach_customer_final_response
from app.agent.planner import IntentPlanner
from app.agent.router import AgentRouter
from app.agent.state import AgentState
from app.anchors import AnchorGateway, AnchorRegistry
from app.audit import TraceStore, create_trace_store
from app.llm.qwen_general_chat import QwenGeneralChat
from app.rag import LocalRAGAnswerer
from app.rules import PermissionChecker, SafetyChecker
from app.schemas.request import AgentChatRequest
from app.schemas.response import AgentResponse, UIEvent
from app.schemas.route import RouteDecision
from app.schemas.trace import TraceRecord, TraceStep
from app.skills.context import SkillContext
from app.skills.registry import SkillRegistry
from app.dialogue.orchestrator import DialogueOrchestrator
from app.dialogue.ledger import default_model_ledger
from app.qwen_gateway.telemetry import default_qwen_ledger
from app.rag.ledger import default_rag_ledger


class GraphState(TypedDict):
    agent_state: AgentState


class LangGraphAgentWorkflow:
    def __init__(
        self,
        anchor_registry: AnchorRegistry | None = None,
        safety_checker: SafetyChecker | None = None,
        permission_checker: PermissionChecker | None = None,
        agent_router: AgentRouter | None = None,
        intent_planner: IntentPlanner | None = None,
        general_chat: QwenGeneralChat | None = None,
        rag_answerer: LocalRAGAnswerer | None = None,
        anchor_gateway: AnchorGateway | None = None,
        trace_store: TraceStore | None = None,
        skill_registry: SkillRegistry | None = None,
        dialogue_orchestrator: DialogueOrchestrator | None = None,
    ) -> None:
        self.anchor_registry = anchor_registry or AnchorRegistry()
        self.safety_checker = safety_checker or SafetyChecker()
        self.permission_checker = permission_checker or PermissionChecker()
        self.agent_router = agent_router or AgentRouter()
        self.intent_planner = intent_planner or IntentPlanner()
        self.general_chat = general_chat or QwenGeneralChat()
        self.rag_answerer = rag_answerer or LocalRAGAnswerer()
        self.anchor_gateway = anchor_gateway or AnchorGateway()
        self.trace_store = trace_store or create_trace_store()
        self.dialogue_orchestrator = dialogue_orchestrator or DialogueOrchestrator()
        self.skill_registry = skill_registry or SkillRegistry.default(
            anchor_registry=self.anchor_registry,
            intent_planner=self.intent_planner,
            permission_checker=self.permission_checker,
            general_chat=self.general_chat,
            rag_answerer=self.rag_answerer,
            anchor_gateway=self.anchor_gateway,
        )
        if hasattr(self.general_chat, "set_capability_provider"):
            self.general_chat.set_capability_provider(self.skill_registry.customer_capabilities)
        self.graph = self._build_graph()

    def run(self, request: AgentChatRequest) -> AgentResponse:
        state = AgentState(request=request.model_copy(deep=True), trace_id=f"trace_{uuid4().hex}", original_query=request.query)
        result = self.graph.invoke({"agent_state": state})
        final_response = result["agent_state"].final_response
        if final_response is None:
            raise RuntimeError("LangGraph workflow finished without AgentResponse")
        return attach_customer_final_response(final_response, request.query)

    def _build_graph(self):
        graph = StateGraph(GraphState)
        graph.add_node("receive_request", self._receive_request)
        graph.add_node("safety_precheck", self._safety_precheck)
        graph.add_node("semantic_orchestration", self._semantic_orchestration)
        graph.add_node("route_decision", self._route_decision)
        graph.add_node("skill_dispatch", self._skill_dispatch)
        graph.add_node("final_response", self._final_response)
        graph.add_node("save_trace", self._save_trace)

        graph.set_entry_point("receive_request")
        graph.add_edge("receive_request", "safety_precheck")
        graph.add_conditional_edges(
            "safety_precheck",
            self._route_after_safety,
            {"blocked": "final_response", "continue": "semantic_orchestration"},
        )
        graph.add_conditional_edges(
            "semantic_orchestration",
            self._route_after_semantic,
            {"clarify": "final_response", "continue": "route_decision"},
        )
        graph.add_edge("route_decision", "skill_dispatch")
        graph.add_edge("skill_dispatch", "final_response")
        graph.add_edge("final_response", "save_trace")
        graph.add_edge("save_trace", END)
        return graph.compile()

    def _receive_request(self, graph_state: GraphState) -> GraphState:
        state = graph_state["agent_state"]
        self._record_step(
            state=state,
            step_name="receive_request",
            status="success",
            input_summary=state.request.query,
            output_summary=f"trace_id={state.trace_id}",
        )
        return graph_state

    def _semantic_orchestration(self, graph_state: GraphState) -> GraphState:
        state = graph_state["agent_state"]
        state.semantic_turn = self._run_step(
            state=state,
            step_name="semantic_orchestration",
            input_summary=state.original_query or state.request.query,
            action=lambda: self.dialogue_orchestrator.prepare(state.request),
            output_summary=lambda turn: f"route_source={turn.route_audit.route_source}, normalized={turn.normalization.normalized_text[:100]}",
        )
        state.request.query = state.semantic_turn.rewritten_query
        if state.semantic_turn.plan is not None and state.semantic_turn.plan.response_intent == "cancel":
            state.final_response = AgentResponse(
                trace_id=state.trace_id,
                status="canceled",
                answer="已取消上一项待澄清任务。",
                intent="cancel_clarification",
                scenario="dialogue_clarification",
                ui_events=[UIEvent(type="show_message", message="已取消上一项待澄清任务。", payload={"task_status": "canceled"})],
                metadata=self._semantic_metadata(state),
            )
        if state.semantic_turn.clarification is not None:
            clarification = state.semantic_turn.clarification
            state.final_response = AgentResponse(
                trace_id=state.trace_id,
                status="clarification_required",
                answer=clarification.question,
                intent=state.semantic_turn.plan.intent if state.semantic_turn.plan else "clarification",
                scenario="dialogue_clarification",
                missing_params=[clarification.missing_field],
                ui_events=[UIEvent(type="ask_clarification", message=clarification.question, payload={"plan_id": clarification.plan_id, "candidates": clarification.candidates})],
                metadata=self._semantic_metadata(state),
            )
        return graph_state

    def _route_after_semantic(self, graph_state: GraphState) -> Literal["clarify", "continue"]:
        return "clarify" if graph_state["agent_state"].final_response is not None else "continue"

    def _safety_precheck(self, graph_state: GraphState) -> GraphState:
        state = graph_state["agent_state"]
        result = self._run_step(
            state=state,
            step_name="safety_precheck",
            input_summary=state.request.query,
            action=lambda: self.safety_checker.check(state.request.query),
            output_summary=lambda safety_result: f"blocked={safety_result['blocked']}",
        )
        if result["blocked"]:
            state.status = "blocked"
            state.final_response = AgentResponse(
                trace_id=state.trace_id,
                status="blocked",
                answer=result["message"],
                intent=None,
                scenario=None,
                route_type=None,
                missing_params=[],
                tool_calls=[],
                evidence_list=[],
                ui_events=[
                    UIEvent(
                        type="safety_block",
                        message=result["message"],
                        payload={"matched_keywords": result["matched_keywords"]},
                    )
                ],
                risk_level="high",
                manual_check_required=True,
            )
        return graph_state

    def _route_decision(self, graph_state: GraphState) -> GraphState:
        state = graph_state["agent_state"]
        state.route_decision = self._run_step(
            state=state,
            step_name="route_decision",
            input_summary=state.request.query,
            action=lambda: self.agent_router.decide(state.request),
            output_summary=self._route_decision_summary,
        )
        print(
            "[agent/chat] route_decision "
            f"{state.route_decision.model_dump(exclude_none=True)}"
        )
        return graph_state

    def _skill_dispatch(self, graph_state: GraphState) -> GraphState:
        state = graph_state["agent_state"]
        state.skill_result = self._run_step(
            state=state,
            step_name="skill_dispatch",
            input_summary=f"route_type={state.route_decision.route_type}",
            action=lambda: self._dispatch_skill(state),
            output_summary=self._skill_result_summary,
        )
        return graph_state

    def _dispatch_skill(self, state: AgentState):
        decision = getattr(state, "route_decision", None)
        if decision is None:
            decision = RouteDecision(
                route_type="unknown",
                reason="missing route decision",
                confidence=0,
            )
            state.route_decision = decision

        if hasattr(self.skill_registry, "get_for_decision"):
            skill = self.skill_registry.get_for_decision(decision)
        else:
            skill = self.skill_registry.get_by_route_type(decision.route_type)
        print(
            "[agent/chat] selected_skill "
            f"skill_id={skill.skill_id!r} route_type={skill.route_type!r}"
        )
        context = SkillContext(
            request=state.request,
            route_decision=decision,
            trace_id=state.trace_id,
            anchors=None,
            user_roles=[state.request.role],
            metadata={"workflow": self.__class__.__name__},
        )
        return skill.run(context)

    def _final_response(self, graph_state: GraphState) -> GraphState:
        state = graph_state["agent_state"]
        if state.final_response is None:
            state.final_response = build_final_response(state)
        state.final_response.metadata.update(self._semantic_metadata(state))
        print(
            "[agent/chat] final_response "
            f"status={state.final_response.status!r} "
            f"ui_events_count={len(state.final_response.ui_events)} "
            f"ui_events={[event.model_dump(exclude_none=True) for event in state.final_response.ui_events]}"
        )

        self._record_step(
            state=state,
            step_name="final_response",
            status=state.final_response.status,
            input_summary="build final response",
            output_summary=f"status={state.final_response.status}",
        )
        return graph_state

    def _semantic_metadata(self, state: AgentState) -> dict:
        turn = state.semantic_turn
        if turn is None:
            return {}
        return {
            "semantic_orchestration": {
                "original_text": turn.normalization.original_text,
                "normalized_text": turn.normalization.normalized_text,
                "quantities": [item.model_dump() for item in turn.normalization.quantities],
                "resolved_references": [item.model_dump(exclude_none=True) for item in turn.resolutions],
                "route": turn.route_audit.model_dump(exclude_none=True),
                "candidate_plan": turn.plan.model_dump(exclude_none=True) if turn.plan and turn.route_audit.route_source == "rule" else None,
                "planner_plan": turn.plan.model_dump(exclude_none=True) if turn.plan and turn.route_audit.route_source in {"qwen", "hybrid"} and turn.model_participation.get("planner_status") == "success" else None,
                "fallback_plan": turn.plan.model_dump(exclude_none=True) if turn.plan and turn.model_participation.get("planner_status") == "fallback" else None,
                "grounded_plan": turn.grounding.grounded_plan if turn.grounding else None,
                "executed_plan": None,
                "executed_ui_events": [],
                "final_status": "awaiting_clarification" if turn.clarification else "pending_execution",
                "final_plan": turn.plan.model_dump(exclude_none=True) if turn.plan else None,
                "grounding": turn.grounding.model_dump(exclude_none=True) if turn.grounding else None,
            },
            "dialogue_state": turn.state.model_dump(exclude_none=True),
            "model_participation": turn.model_participation,
            "model_participation_history": default_model_ledger.list(state.request.session_id),
            "qwen_invocations": default_qwen_ledger.list(state.request.session_id),
            "qwen_fallbacks": [item for item in default_qwen_ledger.list(state.request.session_id) if item.get("fallback_used") or item.get("status") in {"fallback", "timeout", "circuit_open"}],
            **self._rag_metadata(state),
            "clarifications": turn.state.clarification_history + ([turn.clarification.model_dump(exclude_none=True)] if turn.clarification else []),
        }

    def _rag_metadata(self, state: AgentState) -> dict:
        skill_result = getattr(state, "skill_result", None)
        skill_metadata = getattr(skill_result, "metadata", {}) if skill_result else {}
        records = skill_metadata.get("rag_retrievals") or default_rag_ledger.list(state.trace_id)
        evidences = skill_metadata.get("rag_evidences", [])
        citations = skill_metadata.get("rag_citations", skill_metadata.get("citations", []))
        fallbacks = skill_metadata.get("rag_fallbacks", [])
        summary = {
            "triggered": bool(records or skill_metadata.get("skill_id") == "rag_qa"),
            "retrieval_status": skill_metadata.get("retrieval_status"),
            "retrieval_method": skill_metadata.get("retrieval_method"),
            "candidate_count": skill_metadata.get("candidate_count"),
            "returned_evidence_count": skill_metadata.get("returned_evidence_count", len(evidences)),
            "top_score": skill_metadata.get("top_score"),
            "retrieval_latency_ms": skill_metadata.get("retrieval_latency_ms"),
            "generation_invoked": skill_metadata.get("generation_invoked", False),
            "generation_status": skill_metadata.get("generation_status"),
            "fallback_used": skill_metadata.get("fallback_used", False),
            "citation_validation": skill_metadata.get("citation_validation", "none"),
            "citation_coverage": skill_metadata.get("citation_coverage", "none"),
            "fused_count": skill_metadata.get("fused_count"),
            "lexical_invoked": skill_metadata.get("lexical_invoked", False),
            "dense_invoked": skill_metadata.get("dense_invoked", False),
            "reranker_invoked": skill_metadata.get("reranker_invoked", False),
            "embedding_model": skill_metadata.get("embedding_model"),
            "reranker_model": skill_metadata.get("reranker_model"),
            "index_version": skill_metadata.get("index_version"),
            "index_status": skill_metadata.get("index_status"),
        }
        return {
            "rag": summary,
            "rag_retrievals": records,
            "rag_evidences": evidences,
            "rag_citations": citations,
            "rag_fallbacks": fallbacks,
            "rag_source_operations": skill_metadata.get("rag_source_operations", []),
            "rag_index_jobs": skill_metadata.get("rag_index_jobs", []),
            "rag_backup_operations": skill_metadata.get("rag_backup_operations", []),
            "rag_index_status": skill_metadata.get("rag_index_status", {"active_index_version": skill_metadata.get("index_version"), "status": skill_metadata.get("index_status")}),
        }

    def _save_trace(self, graph_state: GraphState) -> GraphState:
        state = graph_state["agent_state"]
        response = state.final_response
        if response is None:
            response = build_final_response(state)
            state.final_response = response

        self._record_step(
            state=state,
            step_name="save_trace",
            status="success",
            input_summary="persist trace record",
            output_summary=f"trace_id={state.trace_id}",
        )
        record = TraceRecord(
            trace_id=state.trace_id,
            session_id=state.request.session_id,
            user_id=state.request.user_id,
            query=state.original_query or state.request.query,
            steps=state.trace_steps,
            status=response.status,
        )
        self.trace_store.save(record)
        return graph_state

    def _route_after_safety(
        self,
        graph_state: GraphState,
    ) -> Literal["blocked", "continue"]:
        state = graph_state["agent_state"]
        return "blocked" if state.final_response and state.final_response.status == "blocked" else "continue"

    def _route_decision_summary(self, decision: RouteDecision) -> str:
        return (
            f"route_type={decision.route_type}, reason={decision.reason}, "
            f"confidence={decision.confidence}, router={decision.router}"
        )

    def _skill_result_summary(self, result) -> str:
        print(
            "[agent/chat] skill_result "
            f"status={result.status!r} "
            f"ui_events_count={len(result.ui_events)} "
            f"ui_events={[event.model_dump(exclude_none=True) for event in result.ui_events]}"
        )
        metadata = result.metadata
        return (
            f"skill_id={metadata.get('skill_id')}, "
            f"route_type={metadata.get('route_type')}, "
            f"status={result.status}, scenario={result.scenario}, "
            f"latency_ms={metadata.get('latency_ms')}, "
            f"timeout_exceeded={metadata.get('timeout_exceeded')}, "
            f"error_type={metadata.get('error_type')}, "
            f"evidence_count={metadata.get('evidence_count')}, "
            f"tool_calls_count={metadata.get('tool_calls_count')}, "
            f"ui_events_count={metadata.get('ui_events_count')}"
        )

    def _run_step(self, state, step_name, input_summary, action, output_summary):
        start = perf_counter()
        try:
            result = action()
            self._record_step(
                state=state,
                step_name=step_name,
                status="success",
                input_summary=input_summary,
                output_summary=output_summary(result),
                latency_ms=self._latency_ms(start),
            )
            return result
        except Exception as exc:
            self._record_step(
                state=state,
                step_name=step_name,
                status="failed",
                input_summary=input_summary,
                output_summary="failed",
                error=str(exc),
                latency_ms=self._latency_ms(start),
            )
            raise

    def _record_step(
        self,
        state: AgentState,
        step_name: str,
        status: str,
        input_summary: str,
        output_summary: str,
        error: str | None = None,
        latency_ms: int = 0,
    ) -> None:
        state.trace_steps.append(
            TraceStep(
                step_name=step_name,
                status=status,
                input_summary=input_summary,
                output_summary=output_summary,
                error=error,
                latency_ms=latency_ms,
            )
        )

    def _latency_ms(self, start: float) -> int:
        return max(0, int((perf_counter() - start) * 1000))
