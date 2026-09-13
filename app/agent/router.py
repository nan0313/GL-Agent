import re

from app.llm.qwen_router import QwenRouter
from app.schemas.request import AgentChatRequest
from app.schemas.route import RouteDecision


def _u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


_DETERMINISTIC_WEBGL_KEYWORDS = [
    _u("\\u7f13\\u51b2\\u533a"),
    _u("\\u5411\\u5916\\u7f13\\u51b2"),
    "查找",
    "找到",
    _u("\\u67e5\\u770b\\u8fd9\\u4e2a\\u5bf9\\u8c61\\u7684\\u5b9e\\u65f6\\u6570\\u636e"),
    _u("\\u67e5\\u770b\\u9019\\u500b\\u5c0d\\u8c61\\u7684\\u5be6\\u6642\\u6578\\u64da"),
    _u("\\u5b9e\\u65f6\\u6570\\u636e"),
    _u("\\u5be6\\u6642\\u6578\\u64da"),
    _u("\\u6253\\u5f00\\u9762\\u677f"),
    _u("\\u6253\\u958b\\u9762\\u677f"),
    _u("\\u5bf9\\u8c61\\u9762\\u677f"),
    _u("\\u5bf9\\u8c61\\u4fe1\\u606f"),
    _u("\\u67e5\\u770b\\u5f53\\u524d\\u5bf9\\u8c61"),
    _u("\\u67e5\\u770b\\u8fd9\\u4e2a\\u5bf9\\u8c61\\u7684\\u4fe1\\u606f"),
    _u("\\u5bf9\\u8c61\\u5c5e\\u6027"),
    _u("\\u83b7\\u53d6\\u56fe\\u5c42\\u5217\\u8868"),
    _u("\\u56fe\\u5c42\\u5217\\u8868"),
    _u("\\u56fe\\u5c42\\u6811"),
    _u("\\u5f53\\u524d\\u6709\\u54ea\\u4e9b\\u56fe\\u5c42"),
    _u("\\u5217\\u51fa\\u6240\\u6709\\u56fe\\u5c42"),
    _u("\\u54ea\\u4e9b\\u56fe\\u5c42\\u6b63\\u5728\\u663e\\u793a"),
    _u("\\u54ea\\u4e9b\\u56fe\\u5c42\\u88ab\\u9690\\u85cf"),
    _u("\\u8fd9\\u4e2a\\u5bf9\\u8c61\\u662f\\u4ec0\\u4e48"),
    _u("\\u521a\\u624d\\u9009\\u4e2d\\u4e86\\u4ec0\\u4e48"),
    _u("\\u521a\\u624d\\u5b9a\\u4f4d\\u5bf9\\u8c61"),
    _u("\\u663e\\u793a\\u56fe\\u5c42"),
    _u("\\u9690\\u85cf\\u56fe\\u5c42"),
    _u("\\u9690\\u85cf\\u5f71\\u50cf"),
    _u("\\u663e\\u793a\\u5f71\\u50cf"),
    _u("\\u9690\\u85cf\\u5730\\u5f62"),
    _u("\\u663e\\u793a\\u5730\\u5f62"),
    _u("\\u5f53\\u524d\\u76f8\\u673a"),
    _u("\\u76f8\\u673a\\u72b6\\u6001"),
    _u("\\u89c6\\u89d2\\u4fe1\\u606f"),
    _u("\\u590d\\u4f4d\\u89c6\\u89d2"),
    _u("\\u6062\\u590d\\u89c6\\u89d2"),
    _u("\\u98de\\u5230\\u8fd9\\u4e2a\\u5bf9\\u8c61"),
    _u("\\u98de\\u5230\\u5f53\\u524d\\u5bf9\\u8c61"),
    _u("\\u98de\\u5230\\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61"),
    _u("\\u5b9a\\u4f4d\\u5230\\u5f53\\u524d\\u5bf9\\u8c61"),
    _u("\\u9ad8\\u4eae\\u5f53\\u524d\\u5bf9\\u8c61"),
    _u("\\u9ad8\\u4eae\\u8fd9\\u4e2a\\u5bf9\\u8c61"),
    _u("\\u9ad8\\u4eae"),
    _u("\\u53d6\\u6d88\\u9ad8\\u4eae"),
    _u("\\u6e05\\u9664\\u6807\\u8bb0"),
    _u("\\u67e5\\u8be2\\u9644\\u8fd1"),
    _u("\\u67e5\\u627e\\u9644\\u8fd1"),
    _u("\\u67e5\\u770b\\u9644\\u8fd1"),
    _u("\\u9644\\u8fd1\\u6709\\u4ec0\\u4e48"),
    _u("\\u9ad8\\u4eae\\u9644\\u8fd1"),
    _u("\\u98de\\u5230\\u9644\\u8fd1"),
    _u("\\u5b9a\\u4f4d\\u9644\\u8fd1"),
    _u("\\u5468\\u56f4"),
    _u("\\u8303\\u56f4\\u5185"),
    _u("\\u7c73\\u4ee5\\u5185"),
    _u("\\u7c73\\u5185"),
    _u("\\u98de\\u5230"),
    _u("\\u5b9a\\u4f4d\\u5230\\u5750\\u6807"),
    _u("\\u622a\\u56fe"),
    _u("\\u83b7\\u53d6\\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61"),
    _u("\\u67e5\\u770b\\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61"),
]

_RAG_KEYWORDS = [
    _u("\\u89c4\\u7a0b"),
    _u("\\u5236\\u5ea6"),
    _u("\\u624b\\u518c"),
    _u("\\u89c4\\u8303"),
    _u("\\u77e5\\u8bc6\\u5e93"),
    _u("\\u6587\\u6863\\u4f9d\\u636e"),
    _u("\\u6839\\u636e"),
    _u("\\u6d41\\u7a0b"),
    "附件",
    "上传的文件",
    "会话文件",
    "项目资料",
    "文档里",
    "输变电工程",
    "三维设计模型",
    "模型层级",
    "架空线路",
    "电缆线路",
    "逻辑模型",
    "国家高程基准",
    "坐标系统",
    "坐标系",
    "高程基准",
]

_ANCHOR_KEYWORDS = [
    "飞到",
    "高亮",
    "属性",
    "行政区",
    _u("\\u641c\\u7d22"),
    _u("\\u5b9a\\u4f4d"),
    _u("\\u6253\\u5f00"),
    _u("\\u67e5\\u770b"),
    _u("\\u9762\\u677f"),
    _u("\\u9009\\u4e2d\\u5bf9\\u8c61"),
    _u("\\u5730\\u56fe"),
    _u("\\u56fe\\u5c42"),
    _u("\\u8bbe\\u5907"),
    _u("\\u9988\\u7ebf"),
    _u("\\u53d8\\u7535\\u7ad9"),
    _u("\\u7ebf\\u8def"),
    _u("\\u8fd0\\u884c\\u60c5\\u51b5"),
    _u("\\u6570\\u636e\\u9762\\u677f"),
]

_GENERAL_KEYWORDS = [
    _u("\\u4f60\\u662f\\u8c01"),
    _u("\\u4f60\\u80fd\\u505a\\u4ec0\\u4e48"),
    _u("\\u89e3\\u91ca"),
    _u("\\u4ec0\\u4e48\\u662f"),
    _u("\\u4e3a\\u4ec0\\u4e48"),
    _u("\\u600e\\u4e48\\u7406\\u89e3"),
    _u("\\u4eca\\u5929\\u5929\\u6c14\\u600e\\u4e48\\u6837"),
    _u("\\u5929\\u6c14"),
    _u("\\u95f2\\u804a"),
    _u("\\u4f60\\u597d"),
]

_DOCUMENT_EXPLICIT_KEYWORDS = [
    "文件", "附件", "文档", "上传的", "刚才上传", "刚刚上传",
    "这份pdf", "这份 PDF", "这份word", "这份 Word", "项目方案",
]

_DOCUMENT_FOLLOW_UP_TERMS = (
    "多少", "是什么", "哪些", "哪几", "何时", "什么时候", "是否", "有没有",
    "在哪", "怎么", "如何", "为什么", "包括", "包含", "要求", "规定", "区别", "比较",
)
_NON_DOCUMENT_UTTERANCES = (
    "烂系统", "垃圾系统", "垃圾", "不好用", "谢谢", "多谢", "再见", "你好", "你是谁", "你能做什么",
)

_CAPABILITY_QUERY_MARKERS = (
    "你是谁", "你能做什么", "你的能力", "能帮我干嘛", "可以做什么",
    "你能操作gis", "你能操作地图", "你能操作webgl", "你可以操作gis",
    "你可以操作地图", "你可以帮我", "你能控制真实", "你可以控制真实",
)


class AgentRouter:
    def __init__(self, qwen_router: QwenRouter | None = None) -> None:
        self.qwen_router = qwen_router or QwenRouter()

    def decide(self, request: AgentChatRequest) -> RouteDecision:
        rule_decision = self._rule_decision(request)
        if rule_decision.route_type != "unknown":
            return rule_decision

        try:
            return self.qwen_router.decide(request)
        except Exception:
            return RouteDecision(
                route_type="general_chat",
                reason="no business route matched; use the general conversation fallback",
                confidence=0.35,
                need_tool=False,
                need_rag=False,
                rewritten_query=request.query,
                router="general_fallback",
                skill_name="general_chat",
                route_name="general_conversation_fallback",
                source="deterministic_router",
            )

    def _rule_decision(self, request: AgentChatRequest) -> RouteDecision:
        query = request.query
        normalized = query.strip().lower()

        if _is_capability_query(normalized):
            return RouteDecision(
                route_type="general_chat",
                reason="query asks about current customer capabilities",
                confidence=1.0,
                need_tool=False,
                need_rag=False,
                rewritten_query=query,
                router="capability_registry",
                skill_name="general_chat",
                route_name="customer_capability_awareness",
                source="deterministic_router",
            )

        if any(keyword in normalized for keyword in _DETERMINISTIC_WEBGL_KEYWORDS):
            return RouteDecision(
                route_type="anchor_task",
                reason="matched explicit WebGL realtime-data anchor command",
                confidence=1.0,
                need_tool=False,
                need_rag=False,
                rewritten_query=query,
                router="deterministic_webgl",
                skill_name="deterministic_webgl_anchor",
                route_name="webgl_deterministic_anchor",
                source="deterministic_router",
            )

        if any(keyword.casefold() in normalized for keyword in _DOCUMENT_EXPLICIT_KEYWORDS):
            return RouteDecision(
                route_type="document_task",
                reason="query explicitly refers to a conversation attachment",
                confidence=0.99,
                need_tool=False,
                need_rag=True,
                rewritten_query=query,
                router="document_rule",
            )

        if any(keyword in normalized for keyword in _RAG_KEYWORDS):
            return RouteDecision(
                route_type="rag_qa",
                reason="query mentions rules, documents, procedures, or knowledge base",
                confidence=0.86,
                need_tool=False,
                need_rag=True,
                rewritten_query=query,
                router="rule",
            )

        if any(keyword in normalized for keyword in _ANCHOR_KEYWORDS):
            return RouteDecision(
                route_type="anchor_task",
                reason="query requires WebGL, GIS, object, or anchor tool interaction",
                confidence=0.9,
                need_tool=True,
                need_rag=False,
                rewritten_query=query,
                router="rule",
            )

        if any(keyword in normalized for keyword in _GENERAL_KEYWORDS):
            return RouteDecision(
                route_type="general_chat",
                reason="query is a general question and does not require anchor tools",
                confidence=0.84,
                need_tool=False,
                need_rag=False,
                rewritten_query=query,
                router="rule",
            )

        if self._has_document_context(request) and self._looks_like_document_follow_up(query):
            return RouteDecision(
                route_type="document_task",
                reason="conversation has an active or selected attachment for document follow-up",
                confidence=0.92,
                need_tool=False,
                need_rag=True,
                rewritten_query=query,
                router="document_context",
            )

        return RouteDecision(
            route_type="unknown",
            reason="no routing rule matched",
            confidence=0.0,
            need_tool=False,
            need_rag=False,
            rewritten_query=query,
            router="rule",
        )

    @staticmethod
    def _has_document_context(request: AgentChatRequest) -> bool:
        conversation_id = request.conversation_id or request.session_id
        if not conversation_id:
            return False
        try:
            from app.conversations.attachments import get_default_attachment_service
            from app.dialogue.store import default_dialogue_store

            snapshot = get_default_attachment_service().state_snapshot(conversation_id, request.user_id)
            state = default_dialogue_store.get(conversation_id)
            return bool(
                state.document_context.selected_attachment_ids
                or int(snapshot.get("active_attachment_count") or 0) == 1
            )
        except Exception:
            return False

    @staticmethod
    def _looks_like_document_follow_up(query: str) -> bool:
        normalized = re.sub(r"[\s，。！？?!,.]", "", str(query or "")).casefold()
        if not normalized or any(term in normalized for term in _NON_DOCUMENT_UTTERANCES):
            return False
        if any(term in normalized for term in _DOCUMENT_FOLLOW_UP_TERMS):
            return True
        return bool(re.match(r"^(?:它|里面|其中|这个|该|上述|刚才)(?:的|里面|中)?", normalized))


def _is_capability_query(query: str) -> bool:
    normalized = re.sub(r"\s+", "", str(query or "")).casefold()
    return any(marker in normalized for marker in _CAPABILITY_QUERY_MARKERS)
