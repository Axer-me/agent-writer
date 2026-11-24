"""В сцену попадают строки, где есть её имена, а не начало файла."""

from __future__ import annotations

import re

_RANKS = frozenset(
    "полковник капитан лейтенант сержант поручик майор генерал князь граф прапорщик подполковник".split()
)
_PLAN_LABELS = frozenset(
    "глава теги кратко читатель события концовка начинается изменение участники".split()
)


def person_tokens(full_name: str) -> list[str]:
    """Фамилия и имя. Звание в начале строки именем не считается."""
    parts = re.findall(r"[А-ЯЁA-Z][а-яёa-z-]{2,}", full_name or "")
    personal = [part for part in parts if part.lower() not in _RANKS]
    if not personal:
        personal = parts
    ordered: list[str] = []
    for token in (personal[-1:] + personal[:1]):
        if token and token not in ordered:
            ordered.append(token)
    return ordered


def _stem(token: str) -> str:
    low = token.lower().replace("ё", "е")
    if len(low) >= 6:
        return low[:-1]
    return low


def mentioned(text: str, full_name: str, call: str = "") -> bool:
    low = (text or "").lower().replace("ё", "е")
    tokens = person_tokens(call) + person_tokens(full_name)
    seen: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    return any(_stem(token) in low for token in seen if len(token) >= 3)


def chapter_needles(plan_text: str, characters: list[dict] | None = None) -> list[str]:
    found: list[str] = []
    low = (plan_text or "").lower()
    for item in characters or []:
        tokens = person_tokens(item.get("name") or "")
        if not any(token.lower() in low for token in tokens):
            continue
        for token in tokens:
            if token not in found:
                found.append(token)
    for word in re.findall(r"[А-ЯЁ][а-яё-]{2,}", plan_text or ""):
        if word.lower() in _PLAN_LABELS or word.lower() in _RANKS:
            continue
        if word not in found:
            found.append(word)
    return found[:12]


def lines_about(text: str, needles: list[str], limit: int = 2500) -> str:
    body = text or ""
    keys = [needle.lower() for needle in needles if len(needle) >= 3]
    if not keys:
        return body[:limit]
    picked: list[str] = []
    for paragraph in re.split(r"\n\s*\n", body):
        paragraph = paragraph.strip()
        if paragraph and any(key in paragraph.lower() for key in keys):
            picked.append(paragraph)
    chosen = "\n\n".join(picked)
    if not chosen:
        return body[:limit]
    return chosen[:limit]
