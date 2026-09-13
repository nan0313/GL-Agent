from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator


AnswerDeltaEmitter = Callable[[str], None]
_answer_delta_emitter: ContextVar[AnswerDeltaEmitter | None] = ContextVar(
    "answer_delta_emitter",
    default=None,
)


@contextmanager
def answer_delta_stream(emitter: AnswerDeltaEmitter) -> Iterator[None]:
    token = _answer_delta_emitter.set(emitter)
    try:
        yield
    finally:
        _answer_delta_emitter.reset(token)


def has_answer_delta_stream() -> bool:
    return _answer_delta_emitter.get() is not None


def emit_answer_delta(delta: str) -> bool:
    emitter = _answer_delta_emitter.get()
    text = str(delta or "")
    if emitter is None or not text:
        return False
    try:
        emitter(text)
        return True
    except Exception:
        return False

