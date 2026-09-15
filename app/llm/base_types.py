from __future__ import annotations

from dataclasses import dataclass, field


class PaidModelBlocked(RuntimeError):
    """Попытка вызвать платную модель OpenRouter при нулевом балансе."""


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    censored: bool = False
    error: str | None = None
    finish_reason: str | None = None
    fallback_from: str | None = None
    retries: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and not self.censored and not self.error
