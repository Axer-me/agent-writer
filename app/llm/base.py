from __future__ import annotations

from typing import Protocol

from app.llm.base_types import LLMResult

Message = dict[str, str]


class LLMProvider(Protocol):
    name: str

    def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 2048,
        temperature: float = 0.8,
    ) -> LLMResult: ...
