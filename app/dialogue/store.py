from copy import deepcopy
from threading import RLock
from typing import Any, Protocol

from app.dialogue.capabilities import available_actions
from app.dialogue.models import DialogueState, FocusItem, ObjectReference, ObjectSet, utc_now
from app.schemas.request import GISContext


class DialogueStateRepository(Protocol):
    def get_state(self, conversation_id: str) -> dict[str, Any] | None: ...
    def save_state(self, conversation_id: str, state: dict[str, Any]) -> None: ...


class DialogueStateStore:
    """Server-side conversational state with one-way compatibility imports.

    Browser execution snapshots are authoritative.  A stale exported dialogue_state
    must never overwrite newer adapter ledgers supplied in the same request.
    """

    def __init__(self, repository: DialogueStateRepository | None = None) -> None:
        self._states: dict[str, DialogueState] = {}
        self._lock = RLock()
        self._repository = repository

    def get(self, conversation_id: str) -> DialogueState:
        with self._lock:
            state = self._states.get(conversation_id)
            if state is None and self._repository is not None:
                raw = self._repository.get_state(conversation_id)
                if raw:
                    try:
                        state = DialogueState.model_validate(raw)
                    except Exception:
                        state = None
            if state is None:
                state = DialogueState(conversation_id=conversation_id)
            self._states[conversation_id] = state
            return deepcopy(state)

    def save(self, state: DialogueState) -> None:
        state.updated_at = utc_now()
        with self._lock:
            self._states[state.conversation_id] = deepcopy(state)
            if self._repository is not None:
                self._repository.save_state(state.conversation_id, state.model_dump(mode="json"))

    def clear(self) -> None:
        with self._lock:
            self._states.clear()

    def begin_turn(self, conversation_id: str, gis_context: GISContext) -> DialogueState:
        state = self.get(conversation_id)
        state.turn_index += 1
        self._merge_execution_context(state, gis_context)
        return state

    def _merge_execution_context(self, state: DialogueState, context: GISContext) -> None:
        extra = context.model_extra or {}
        dialogue = extra.get("dialogue_state") if isinstance(extra.get("dialogue_state"), dict) else {}

        # Import legacy/server state once.  On every turn, concrete adapter ledgers
        # below take precedence over the embedded dialogue_state snapshot.
        if not state.legacy_context_imported:
            self._import_legacy_snapshot(state, context, dialogue)
            state.legacy_context_imported = True

        selected = context.selected_object.model_dump(exclude_none=True) if context.selected_object else None
        last_object = extra.get("last_context_object")
        if self._eligible_natural_object(selected):
            self._record_single(state, selected, "selected_object")
        elif self._eligible_natural_object(last_object):
            self._record_single(state, last_object, "last_context_object")

        admin = extra.get("last_admin_region")
        if isinstance(admin, dict):
            self._record_admin(state, admin, "adapter_snapshot")

        buffers = extra.get("buffers")
        if not isinstance(buffers, list):
            candidate = extra.get("last_buffer")
            buffers = [candidate] if isinstance(candidate, dict) else []
        self._reconcile_buffers(state, buffers)

        queries = extra.get("buffer_queries")
        if not isinstance(queries, list):
            candidate = extra.get("last_buffer_query")
            queries = [candidate] if isinstance(candidate, dict) else []
        self._reconcile_queries(state, queries)

    def _import_legacy_snapshot(self, state: DialogueState, context: GISContext, dialogue: dict[str, Any]) -> None:
        extra = context.model_extra or {}
        last_object = dialogue.get("last_single_object") or extra.get("last_context_object")
        admin = dialogue.get("last_admin_region") or extra.get("last_admin_region")
        buffer = dialogue.get("last_buffer") or extra.get("last_buffer")
        query = dialogue.get("last_buffer_query") or extra.get("last_buffer_query")
        if self._eligible_natural_object(last_object):
            self._record_single(state, last_object, "legacy_context")
        if isinstance(admin, dict):
            self._record_admin(state, admin, "legacy_context")
        if isinstance(buffer, dict):
            self._record_buffer(state, buffer, "legacy_context")
        if isinstance(query, dict):
            self._record_query(state, query, "legacy_context")

    @staticmethod
    def _eligible_natural_object(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        rid = str(item.get("reference_id") or item.get("object_id") or item.get("id") or "")
        return bool(rid) and not item.get("is_test_object") and not rid.startswith("MOCK_OBJECT_") and item.get("eligible_for_natural_reference", True) is not False

    def _record_single(self, state: DialogueState, item: dict[str, Any], source: str) -> None:
        state.last_single_object = deepcopy(item)
        rid = str(item.get("reference_id") or item.get("object_id") or item.get("id"))
        state.add_focus(FocusItem(kind="object", reference_id=rid, label=item.get("object_name") or item.get("name"), source_turn=state.turn_index, source_tool=source, available_actions=available_actions("object", item), snapshot=item))

    def _record_admin(self, state: DialogueState, admin: dict[str, Any], source: str) -> None:
        state.last_admin_region = deepcopy(admin)
        rid = str(admin.get("reference_id") or admin.get("region_id") or admin.get("boundary_object_id") or admin.get("adcode") or admin.get("name") or "admin")
        state.add_focus(FocusItem(kind="admin_region", reference_id=rid, label=admin.get("name") or admin.get("object_name"), source_turn=state.turn_index, source_tool=source, available_actions=available_actions("admin_region", admin), snapshot=admin))

    def _record_buffer(self, state: DialogueState, buffer: dict[str, Any], source: str) -> None:
        rid = str(buffer.get("buffer_id") or "")
        if not rid:
            return
        active = bool(buffer.get("rendered", True)) and buffer.get("status") not in {"cleared", "failed", "canceled"}
        state.add_focus(FocusItem(kind="buffer", reference_id=rid, label=buffer.get("display_value") or f"{buffer.get('distance_m', '')} m".strip(), source_turn=state.turn_index, source_tool=source, active=active, available_actions=available_actions("buffer", buffer), snapshot=buffer))
        if active:
            state.last_buffer = deepcopy(buffer)
        elif state.last_buffer and state.last_buffer.get("buffer_id") == rid:
            state.last_buffer = None

    def _reconcile_buffers(self, state: DialogueState, buffers: list[Any]) -> None:
        actual: dict[str, dict[str, Any]] = {str(x.get("buffer_id")): x for x in buffers if isinstance(x, dict) and x.get("buffer_id")}
        if not actual:
            return
        changed = False
        for focus in state.focus_stack:
            if focus.kind == "buffer" and focus.reference_id in actual:
                current = actual[focus.reference_id]
                active = bool(current.get("rendered")) and current.get("status") not in {"cleared", "failed", "canceled"}
                if focus.active != active:
                    changed = True
                focus.active = active
                focus.snapshot = deepcopy(current)
                focus.available_actions = available_actions("buffer", current)
        for entry in actual.values():
            self._record_buffer(state, entry, "adapter_snapshot")
        active = [x for x in actual.values() if x.get("rendered") and x.get("status") not in {"cleared", "failed", "canceled"}]
        latest = active[-1] if active else None
        old_id = (state.last_buffer or {}).get("buffer_id")
        new_id = (latest or {}).get("buffer_id")
        state.last_buffer = deepcopy(latest) if latest else None
        if changed or old_id != new_id:
            state.state_events.append({"type": "STATE_RECONCILED", "entity": "buffer", "from": old_id, "to": new_id, "created_at": utc_now()})

    def _record_query(self, state: DialogueState, query: dict[str, Any], source: str) -> None:
        if query.get("status") in {"failed", "cleared", "canceled"} or query.get("active", True) is False:
            return
        query_id = str(query.get("query_id") or query.get("set_id") or "")
        if not query_id:
            return
        state.last_buffer_query = deepcopy(query)
        raw_items = query.get("items") or query.get("matches") or query.get("objects") or []
        items = [ObjectReference(reference_id=str(x.get("reference_id") or x.get("object_id") or x.get("id")), name=x.get("name") or x.get("object_name"), object_type=x.get("object_type"), rank=i + 1, data=x) for i, x in enumerate(raw_items) if self._eligible_natural_object(x)]
        obj_set = ObjectSet(set_id=query_id, source_tool=str(query.get("source_tool") or "query_objects_in_buffer"), items=items, count=len(items), active=True)
        state.last_object_set = obj_set
        state.add_focus(FocusItem(kind="object_set", reference_id=query_id, label=f"查询结果（{len(items)}项）", source_turn=state.turn_index, source_tool=source, plural=True, available_actions=available_actions("object_set", query), snapshot=obj_set.model_dump()))
        state.add_focus(FocusItem(kind="buffer_query", reference_id=query_id, label="缓冲区查询", source_turn=state.turn_index, source_tool=source, plural=True, available_actions=available_actions("buffer_query", query), snapshot=query))

    def _reconcile_queries(self, state: DialogueState, queries: list[Any]) -> None:
        valid = [x for x in queries if isinstance(x, dict) and x.get("query_id") and x.get("status") not in {"failed", "cleared", "canceled"}]
        if not valid:
            return
        latest = valid[-1]
        active_buffer_ids = {f.reference_id for f in state.active_focuses("buffer")}
        if latest.get("buffer_id") and latest.get("buffer_id") not in active_buffer_ids:
            state.invalidate("object_set", "buffer_query")
            state.last_buffer_query = None
            state.last_object_set = None
            return
        self._record_query(state, latest, "adapter_snapshot")


class _DefaultConversationStateRepository:
    def get_state(self, conversation_id: str) -> dict[str, Any] | None:
        from app.conversations import get_default_conversation_store

        store = get_default_conversation_store()
        if store.get_conversation(conversation_id) is None:
            return None
        return store.get_state(conversation_id)

    def save_state(self, conversation_id: str, state: dict[str, Any]) -> None:
        from app.conversations import get_default_conversation_store

        store = get_default_conversation_store()
        # Direct workflow tests may not use the HTTP persistence wrapper. Keep
        # those legacy calls process-local instead of inventing an owner.
        if store.get_conversation(conversation_id) is not None:
            store.save_state(conversation_id, state)


default_dialogue_store = DialogueStateStore(repository=_DefaultConversationStateRepository())
