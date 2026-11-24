"""Поздние шаги помечаются устаревшими, если раньше них поменяли текст.

Отдельного утверждения нет: шаг сделан, когда в нём уже есть текст.
"""

from __future__ import annotations


def stage_order(is_original: bool = True) -> list[str]:
    """Авторская книга: драма раньше персонажей. Чужая: сначала кто они уже есть."""
    if is_original:
        return ["card", "style", "drama", "bible", "plan", "chapter"]
    return ["card", "style", "bible", "drama", "plan", "chapter"]


def stale_after(stale: dict | None, source: str, is_original: bool = True) -> dict[str, bool]:
    """Источник только что записан. Шаги, которые от него зависят, старше этой правки."""
    marks = {key: bool((stale or {}).get(key)) for key in ("drama", "bible", "plan")}
    if source == "card":
        marks["drama"] = True
        marks["bible"] = True
        marks["plan"] = True
    elif source == "drama":
        marks["drama"] = False
        marks["plan"] = True
        if is_original:
            marks["bible"] = True
    elif source == "bible":
        marks["bible"] = False
        marks["plan"] = True
        if not is_original:
            marks["drama"] = True
    elif source == "plan":
        marks["plan"] = False
    return marks
