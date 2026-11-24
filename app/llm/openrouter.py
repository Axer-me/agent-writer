from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import openrouter_api_key, openrouter_model
from app.llm.base_types import LLMError, LLMResult, PaidModelBlocked

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
HEADERS_EXTRA = {
    "HTTP-Referer": "http://127.0.0.1:8765",
    "X-Title": "Agent-Pisatel",
}


def _pause(attempt: int, seconds: float | None = None) -> None:
    from app.pipeline.jobs import check_cancel

    if attempt >= 3:
        check_cancel()
        return
    delay = 2 ** attempt if seconds is None else seconds
    deadline = time.monotonic() + delay
    while True:
        check_cancel()
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.4, left))


def is_free_model_id(model_id: str) -> bool:
    return bool(model_id) and model_id.strip().endswith(":free")


def assert_free_model(model_id: str) -> None:
    if not is_free_model_id(model_id):
        raise PaidModelBlocked(
            f"Модель «{model_id}» не бесплатная (:free). "
            "На OpenRouter нет денег — вызываем только slug с суффиксом :free."
        )


def _is_zero_price(pricing: dict[str, Any] | None) -> bool:
    if not pricing:
        return False
    try:
        prompt = float(pricing.get("prompt") or 0)
        completion = float(pricing.get("completion") or 0)
    except (TypeError, ValueError):
        return False
    return prompt == 0.0 and completion == 0.0


def list_free_text_models(timeout: float = 30.0) -> list[dict[str, Any]]:
    with httpx.Client(timeout=timeout) as client:
        response = client.get(MODELS_URL)
        response.raise_for_status()
        data = response.json().get("data") or []
    out: list[dict[str, Any]] = []
    for item in data:
        model_id = str(item.get("id") or "")
        arch = item.get("architecture") or {}
        modality = str(arch.get("modality") or "")
        if not is_free_model_id(model_id):
            continue
        if not _is_zero_price(item.get("pricing")):
            continue
        if "text" not in modality.split("->")[0] if "->" in modality else True:
            # keep text-capable; skip pure image/audio generators
            pass
        outputs = arch.get("output_modalities") or []
        if outputs and "text" not in outputs:
            continue
        inputs = arch.get("input_modalities") or []
        if inputs and "text" not in inputs:
            continue
        out.append(
            {
                "id": model_id,
                "name": item.get("name") or model_id,
                "context_length": item.get("context_length"),
                "moderated": bool((item.get("top_provider") or {}).get("is_moderated")),
            }
        )
    out.sort(key=lambda row: row["name"].lower())
    return out


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, model: str | None = None) -> None:
        self.model = (model or openrouter_model()).strip()
        assert_free_model(self.model)
        self.api_key = openrouter_api_key()

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 2048,
        temperature: float = 0.8,
    ) -> LLMResult:
        if not self.api_key:
            return LLMResult(
                text="",
                provider=self.name,
                model=self.model,
                error="Нет ключа OpenRouter (OPEN_ROUTER_API_KEY / OPENROUTER_API_KEY).",
            )
        assert_free_model(self.model)
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **HEADERS_EXTRA,
        }
        last_error = "неизвестная ошибка"
        retries = 0
        for attempt in range(4):
            try:
                with httpx.Client(timeout=180.0) as client:
                    response = client.post(OPENROUTER_URL, json=payload, headers=headers)
            except httpx.TimeoutException:
                last_error = "таймаут OpenRouter"
                retries += 1
                _pause(attempt)
                continue
            except httpx.HTTPError as exc:
                last_error = f"сеть OpenRouter: {exc}"
                retries += 1
                _pause(attempt)
                continue

            if response.status_code == 429:
                retries += 1
                wait = float(response.headers.get("Retry-After") or (2**attempt))
                last_error = f"лимит OpenRouter 429 (подождите {wait:.0f}с; у :free часто ~50 запросов/день)"
                _pause(attempt, min(wait, 8))
                continue
            if response.status_code == 402:
                return LLMResult(
                    text="",
                    provider=self.name,
                    model=self.model,
                    error="OpenRouter 402: платная модель или отрицательный баланс. Нужен slug *:free.",
                    retries=retries,
                )
            if response.status_code >= 400:
                last_error = f"OpenRouter HTTP {response.status_code}: {response.text[:400]}"
                if response.status_code >= 500 and attempt < 3:
                    retries += 1
                    _pause(attempt)
                    continue
                return LLMResult(
                    text="",
                    provider=self.name,
                    model=self.model,
                    error=last_error,
                    retries=retries,
                )

            body = response.json()
            choices = body.get("choices") or []
            if not choices:
                return LLMResult(
                    text="",
                    provider=self.name,
                    model=self.model,
                    error="OpenRouter вернул пустой choices",
                    extra=body if isinstance(body, dict) else {},
                    retries=retries,
                )
            message = choices[0].get("message") or {}
            text = (message.get("content") or "").strip()
            reason = choices[0].get("finish_reason")
            used = (body.get("model") or self.model)
            if used and not is_free_model_id(str(used)) and ":" in str(used):
                # router may echo canonical id; still ok if request was :free
                pass
            return LLMResult(
                text=text,
                provider=self.name,
                model=str(used),
                finish_reason=reason,
                retries=retries,
            )

        return LLMResult(
            text="",
            provider=self.name,
            model=self.model,
            error=last_error,
            retries=retries,
        )


def ping_openrouter() -> LLMResult:
    key = openrouter_api_key()
    if not key:
        return LLMResult(
            text="",
            provider="openrouter",
            model=openrouter_model(),
            error="ключ OpenRouter не задан",
        )
    try:
        provider = OpenRouterProvider()
    except PaidModelBlocked as exc:
        return LLMResult(
            text="",
            provider="openrouter",
            model=openrouter_model(),
            error=str(exc),
        )
    return provider.complete(
        [{"role": "user", "content": "Ответь одним словом: ок"}],
        max_tokens=16,
        temperature=0,
    )
