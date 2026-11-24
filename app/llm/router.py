from __future__ import annotations

from typing import Callable, Literal

from app.config import openrouter_model
from app.llm.base_types import LLMResult
from app.llm.gigachat import GigaChatProvider, strip_markdown_fences
from app.llm.openrouter import OpenRouterProvider, assert_free_model

TaskKind = Literal[
    "card",
    "bible",
    "drama",
    "plan",
    "style",
    "chapter",
    "revise",
    "anti_llm",
    "facts",
    "continue",
]

PROSE_TASKS: frozenset[str] = frozenset({"chapter", "revise", "anti_llm", "facts"})


def _complete_unlogged(
    task: TaskKind,
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.8,
) -> LLMResult:
    if task in PROSE_TASKS:
        gc = GigaChatProvider().complete(
            messages, max_tokens=max_tokens, temperature=temperature
        )
        if gc.ok:
            gc.text = strip_markdown_fences(gc.text)
            return gc
        auth_bad = bool(gc.error and ("Ключ авторизации" in gc.error or "401" in gc.error))
        if task == "facts" and auth_bad:
            return gc
        from app.config import openrouter_api_key

        if not openrouter_api_key():
            if gc.censored:
                gc.error = "GigaChat отказал по своим правилам. Запасного ключа нет, шаг остановлен."
            return gc
        fallback = OpenRouterProvider().complete(
            messages, max_tokens=max_tokens, temperature=temperature
        )
        fallback.fallback_from = "gigachat"
        if not fallback.error and gc.censored:
            fallback.extra = {**fallback.extra, "gigachat_censor": True}
        if fallback.ok:
            fallback.text = strip_markdown_fences(fallback.text)
        elif gc.error and not fallback.error:
            fallback.error = gc.error
        elif gc.error:
            fallback.error = (
                f"GigaChat: {gc.error} | OpenRouter: {fallback.error or 'нет текста'}"
            )
        return fallback

    result = OpenRouterProvider().complete(
        messages, max_tokens=max_tokens, temperature=temperature
    )
    if result.ok:
        result.text = strip_markdown_fences(result.text)
        return result
    gc = GigaChatProvider().complete(
        messages, max_tokens=max_tokens, temperature=temperature
    )
    gc.fallback_from = "openrouter"
    if gc.ok:
        gc.text = strip_markdown_fences(gc.text)
        return gc
    if result.error:
        gc.error = (
            f"OpenRouter: {result.error} | GigaChat: {gc.error or 'нет текста'}"
        )
    return gc


def complete_for(
    task: TaskKind,
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.8,
) -> LLMResult:
    import time

    from app.pipeline.jobs import Cancelled, check_cancel, note_call

    check_cancel()
    started = time.perf_counter()
    try:
        result = _complete_unlogged(
            task, messages, max_tokens=max_tokens, temperature=temperature
        )
    except Cancelled:
        raise
    except Exception as exc:
        result = LLMResult(
            text="",
            provider="",
            model="",
            error=f"Запрос к модели не выполнен: {exc.__class__.__name__}: {exc}"[:500],
        )
    note_call(task, result, int((time.perf_counter() - started) * 1000))
    return result


def complete_with_continue(
    task: TaskKind,
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 3500,
    temperature: float = 0.7,
    max_rounds: int = 3,
    on_round: Callable[[int, LLMResult], None] | None = None,
) -> LLMResult:
    """Если ответ обрезан — просим продолжить с места обрыва."""
    combined = ""
    last = complete_for(task, messages, max_tokens=max_tokens, temperature=temperature)
    if on_round:
        on_round(1, last)
    if not last.ok:
        return last
    combined = last.text
    truncated = (last.finish_reason or "").lower() in {"length", "max_tokens", "stop_limit"}
    round_i = 1
    while truncated and round_i < max_rounds:
        round_i += 1
        cont_messages = [
            *messages,
            {"role": "assistant", "content": combined[-6000:]},
            {
                "role": "user",
                "content": "Продолжи с места обрыва. Не повторяй уже написанное. Без предисловий.",
            },
        ]
        nxt = complete_for(
            task, cont_messages, max_tokens=max_tokens, temperature=temperature
        )
        if on_round:
            on_round(round_i, nxt)
        if not nxt.ok:
            break
        combined = (combined.rstrip() + "\n\n" + nxt.text.lstrip()).strip()
        last = nxt
        last.text = combined
        truncated = (nxt.finish_reason or "").lower() in {"length", "max_tokens", "stop_limit"}
    last.text = combined
    return last


def configured_openrouter_ok() -> None:
    assert_free_model(openrouter_model())
