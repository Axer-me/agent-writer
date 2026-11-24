from __future__ import annotations

import re
import time
from typing import Any

from app.config import (
    gigachat_base_url,
    gigachat_credentials,
    gigachat_model,
    gigachat_scope,
    gigachat_verify_ssl,
)
from app.llm.base_types import LLMResult

CENSOR_MARKERS = (
    "не могу",
    "не могу помочь",
    "нарушает",
    "политик",
    "запрещен",
    "запрещён",
    "цензур",
    "не буду",
    "отказываюсь",
    "не допускается",
    "правилам использования",
    "этическ",
)


def _looks_censored(text: str, finish_reason: str | None, extra: dict[str, Any]) -> bool:
    reason = (finish_reason or "").lower()
    if any(flag in reason for flag in ("blacklist", "censor", "filter", "safety", "content_filter")):
        return True
    blob = extra or {}
    for key in ("profanity", "censored", "moderation"):
        if blob.get(key) in (True, "true", 1, "1"):
            return True
    sample = (text or "").strip().lower()
    if not sample:
        return False
    if len(sample) < 400 and any(marker in sample for marker in CENSOR_MARKERS):
        return True
    return False


def _extract_text(response: Any) -> tuple[str, str | None, dict[str, Any]]:
    extra: dict[str, Any] = {}
    finish = None
    # v2-style: messages[*].content[*].text
    messages = getattr(response, "messages", None)
    if messages:
        parts: list[str] = []
        for msg in messages:
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(str(text))
                    elif isinstance(block, dict) and block.get("text"):
                        parts.append(str(block["text"]))
            finish = getattr(msg, "finish_reason", finish)
        if parts:
            return "\n".join(parts).strip(), finish, extra

    choices = getattr(response, "choices", None)
    if choices:
        choice = choices[0]
        finish = getattr(choice, "finish_reason", None) or (
            choice.get("finish_reason") if isinstance(choice, dict) else None
        )
        message = getattr(choice, "message", None)
        if message is None and isinstance(choice, dict):
            message = choice.get("message")
        content = getattr(message, "content", None) if message is not None else None
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, str):
            return content.strip(), finish, extra

    # last resort
    text = str(getattr(response, "content", "") or "")
    return text.strip(), finish, extra


class GigaChatProvider:
    name = "gigachat"

    def __init__(self, model: str | None = None) -> None:
        self.model = (model or gigachat_model()).strip()
        self.credentials = gigachat_credentials()
        self.scope = gigachat_scope()
        self.verify_ssl = gigachat_verify_ssl()
        self._timeout = 180.0

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "credentials": self.credentials,
            "scope": self.scope,
            "model": self.model,
            "verify_ssl_certs": self.verify_ssl,
            "timeout": self._timeout,
        }
        base_url = gigachat_base_url()
        if base_url:
            kwargs["base_url"] = base_url
        return kwargs

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 2048,
        temperature: float = 0.8,
        attempts: int = 4,
    ) -> LLMResult:
        if not self.credentials:
            return LLMResult(
                text="",
                provider=self.name,
                model=self.model,
                error="Нет ключа GigaChat (GIGACHAT_API_KEY / GIGACHAT_CREDENTIALS).",
            )
        cred = self.credentials.strip()
        if len(cred) <= 40 and cred.count("-") >= 3:
            return LLMResult(
                text="",
                provider=self.name,
                model=self.model,
                error=(
                    "В .env похоже Client ID (UUID), а GigaChat SDK ждёт «Ключ авторизации»: "
                    "длинную base64-строку из кабинета developers.sber.ru (Authorization data), "
                    "не Client ID и не Client Secret. Вставьте её в GIGACHAT_API_KEY."
                ),
            )
        try:
            from gigachat import GigaChat
        except ImportError:
            return LLMResult(
                text="",
                provider=self.name,
                model=self.model,
                error="Пакет gigachat не установлен.",
            )

        payload = {
            "model": self.model,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        response = None
        last_error: Exception | None = None
        attempt = 0
        tries = max(1, attempts)
        for attempt in range(tries):
            from app.pipeline.jobs import check_cancel

            check_cancel()
            try:
                with GigaChat(**self._client_kwargs()) as client:
                    response = call_chat(client, payload)
                last_error = None
                break
            except Exception as exc:
                from app.pipeline.jobs import Cancelled

                if isinstance(exc, Cancelled):
                    raise
                last_error = exc
                if _retryable(exc) and attempt < tries - 1:
                    _sleep_retry(min(2 ** (attempt + 1), 8))
                    continue
                break
        if last_error is not None or response is None:
            return LLMResult(
                text="",
                provider=self.name,
                model=self.model,
                error=explain_gigachat_error(last_error or RuntimeError("пустой ответ")),
                retries=attempt,
            )

        text, finish, extra = _extract_text(response)
        censored = _looks_censored(text, finish, extra)
        return LLMResult(
            text="" if censored else text,
            provider=self.name,
            model=self.model,
            censored=censored,
            finish_reason=finish,
            extra={"raw_text": text} if censored else extra,
            error="GigaChat отказал (цензор/политика). Будет запас OpenRouter :free." if censored else None,
        )

def call_chat(client: Any, payload: dict[str, Any]) -> Any:
    """Рабочий вызов v1. Метод chat.create у SDK 0.2.4 уходит на /api/v2 и получает 403."""
    chat = getattr(client, "chat", None)
    if callable(chat):
        return chat(payload)
    create = getattr(chat, "create", None) if chat is not None else None
    if callable(create):
        try:
            return create(
                messages=payload["messages"],
                model=payload.get("model"),
                temperature=payload.get("temperature"),
                max_tokens=payload.get("max_tokens"),
            )
        except TypeError:
            return create(payload)
    raise RuntimeError(
        "SDK GigaChat без совместимого метода chat. Нужна закреплённая версия gigachat==0.2.4."
    )


def _status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    match = re.search(r"\b(401|403|429|500|502|503|504)\b", str(exc))
    return int(match.group(1)) if match else None


def _retryable(exc: Exception) -> bool:
    """Временный сбой сети или сервера. Ключ и отказ доступа сюда не входят."""
    code = _status_code(exc)
    if code in {401, 403}:
        return False
    if code in {429, 500, 502, 503, 504}:
        return True
    upper = str(exc).upper()
    needles = (
        "UNEXPECTED_EOF",
        "SSLEOF",
        "HANDSHAKE",
        "TIMED OUT",
        "TIMEOUT",
        "CONNECTION",
        "CONNECTERROR",
        "RESET",
        "EOF",
        "BROKEN PIPE",
        "UNAVAILABLE",
        "GETADDRINFO",
        "NAME RESOLUTION",
        "CERTIFICATE",
    )
    return any(needle in upper for needle in needles)


def _sleep_retry(seconds: float) -> None:
    from app.pipeline.jobs import check_cancel

    deadline = time.monotonic() + seconds
    while True:
        check_cancel()
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.4, left))


def explain_gigachat_error(exc: Exception) -> str:
    code = _status_code(exc)
    msg = str(exc)
    upper = msg.upper()
    if code == 429 or "TOO MANY REQUESTS" in upper:
        return (
            "GigaChat временно ограничил число запросов (429). "
            "Этот запрос повторён несколько раз. Подождите и запустите шаг снова."
        )
    if code == 401 or "UNAUTHORIZED" in upper:
        return (
            "Ключ GigaChat отклонён (401). В GIGACHAT_API_KEY нужна длинная строка "
            "ключа авторизации, не Client ID."
        )
    if code == 403 and "/api/v2" in msg:
        return (
            "Несовместимый адрес GigaChat API (/api/v2). Задайте "
            "GIGACHAT_BASE_URL=https://api.giga.chat/v1 и перезапустите."
        )
    if "CERTIFICATE_VERIFY_FAILED" in upper or "SELF-SIGNED" in upper:
        return (
            "Сертификат GigaChat не принят. Поставьте GIGACHAT_VERIFY_SSL=false и перезапустите."
        )
    if "UNEXPECTED_EOF" in upper or "SSLEOF" in upper or "HANDSHAKE" in upper or "EOF" in upper:
        return (
            "Соединение с GigaChat оборвалось (TLS). Ключ и адрес тут ни при чём: "
            "запрос повторён несколько раз и не прошёл. Запустите шаг ещё раз."
        )
    if code == 403:
        return f"GigaChat отказал в доступе (403). {msg[:240]}"
    return f"GigaChat: {exc.__class__.__name__}: {msg[:400]}"


def ping_gigachat() -> LLMResult:
    if not gigachat_credentials():
        return LLMResult(
            text="",
            provider="gigachat",
            model=gigachat_model(),
            error="ключ GigaChat не задан",
        )
    provider = GigaChatProvider()
    provider._timeout = 25.0
    return provider.complete(
        [{"role": "user", "content": "Ответь одним словом: ок"}],
        max_tokens=16,
        temperature=0,
        attempts=1,
    )


def strip_markdown_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:\w+)?\n([\s\S]*?)\n```$", text)
    if match:
        return match.group(1).strip()
    return text
