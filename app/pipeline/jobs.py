"""Журнал вызовов модели и отмена главы между шагами."""

from __future__ import annotations

import contextvars

from app.llm.base_types import LLMResult

_book = contextvars.ContextVar("job_book", default=None)
_cancel = contextvars.ContextVar("job_cancel", default=None)


class Cancelled(RuntimeError):
    """Пользователь остановил главу. Уже записанные сцены остаются."""


def bind_job(book, cancel_event) -> tuple:
    return _book.set(book), _cancel.set(cancel_event)


def reset_job(tokens: tuple) -> None:
    _book.reset(tokens[0])
    _cancel.reset(tokens[1])


def check_cancel() -> None:
    event = _cancel.get()
    if event is not None and event.is_set():
        raise Cancelled("Главу остановили. Готовые сцены сохранены.")


def note_call(task: str, result: LLMResult, latency_ms: int) -> None:
    book = _book.get()
    if book is None or result is None:
        return
    from app.pipeline.plan import _store

    _store(book).add_call(
        task,
        result.provider,
        result.model,
        result.retries,
        result.ok,
        result.error or "",
        latency_ms,
    )
