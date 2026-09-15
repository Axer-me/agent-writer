from __future__ import annotations

import re
from typing import Any

from app.config import (
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

    def _client_kwargs(self) -> dict[str, Any]:
        return {
            "credentials": self.credentials,
            "scope": self.scope,
            "model": self.model,
            "verify_ssl_certs": self.verify_ssl,
            "timeout": 180.0,
        }

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 2048,
        temperature: float = 0.8,
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

        try:
            with GigaChat(**self._client_kwargs()) as client:
                response = self._chat(client, payload)
        except Exception as exc:
            msg = str(exc)
            if "CERTIFICATE" in msg.upper() or "SSL" in msg.upper() or "CERT" in msg.upper():
                hint = (
                    "Ошибка сертификата GigaChat. Поставьте в .env строку "
                    "GIGACHAT_VERIFY_SSL=false и перезапустите."
                )
                return LLMResult(
                    text="",
                    provider=self.name,
                    model=self.model,
                    error=f"{hint} ({exc.__class__.__name__})",
                )
            return LLMResult(
                text="",
                provider=self.name,
                model=self.model,
                error=f"GigaChat: {exc.__class__.__name__}: {msg[:400]}",
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

    def _chat(self, client: Any, payload: dict[str, Any]) -> Any:
        chat = getattr(client, "chat", None)
        if chat is None:
            raise RuntimeError("SDK GigaChat без метода chat")
        create = getattr(chat, "create", None)
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
        return chat(payload)


def ping_gigachat() -> LLMResult:
    if not gigachat_credentials():
        return LLMResult(
            text="",
            provider="gigachat",
            model=gigachat_model(),
            error="ключ GigaChat не задан",
        )
    return GigaChatProvider().complete(
        [{"role": "user", "content": "Ответь одним словом: ок"}],
        max_tokens=16,
        temperature=0,
    )


def strip_markdown_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:\w+)?\n([\s\S]*?)\n```$", text)
    if match:
        return match.group(1).strip()
    return text
