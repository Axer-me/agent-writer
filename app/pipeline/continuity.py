from __future__ import annotations

import re

from app.llm.router import complete_for
from app.pipeline.bible import parse_characters
from app.pipeline.prompts import pack
from app.storage.project import BookProject

MISSING_AFTER = 30
REENGAGE_WINDOW = 20


def extract_plan_chapter(plan: str, number: int) -> str:
    pattern = rf"^##\s*Глава\s*{number}\b.*$"
    match = re.search(pattern, plan, flags=re.M | re.I)
    if not match:
        # try "Глава N."
        match = re.search(rf"^##[^\n]*\b{number}\b[^\n]*$", plan, flags=re.M)
    if not match:
        return ""
    start = match.start()
    nxt = re.search(r"^##\s+", plan[match.end() :], flags=re.M)
    end = match.end() + nxt.start() if nxt else len(plan)
    return plan[start:end].strip()


def missing_character_hints(book: BookProject) -> list[dict]:
    state = book.read_state()
    current = int(state.get("chapter_number") or 1)
    last_seen = state.get("last_seen") or {}
    chars = state.get("characters") or parse_characters(book.read_text("bible.md"))
    hints = []
    for ch in chars:
        name = ch.get("name") or ""
        status = ch.get("status") or "alive"
        if status != "alive" or not name:
            continue
        seen = int(last_seen.get(name, 0) or 0)
        if seen == 0:
            gap = current - 1
        else:
            gap = current - seen
        if gap >= MISSING_AFTER:
            hints.append(
                {
                    "name": name,
                    "gap": gap,
                    "message": (
                        f"{name} жив(а), нет записи об уходе, не появляется {gap} гл. "
                        f"Подсказка: в ближайшие {REENGAGE_WINDOW} глав вернуть или объяснить отсутствие. "
                        "Это не автомат — решите сами."
                    ),
                }
            )
    book.patch_state(missing_hints=hints)
    return hints


def update_last_seen_from_text(book: BookProject, chapter_number: int, text: str) -> None:
    state = book.read_state()
    chars = state.get("characters") or parse_characters(book.read_text("bible.md"))
    last_seen = dict(state.get("last_seen") or {})
    body = text
    for ch in chars:
        name = ch.get("name") or ""
        if not name:
            continue
        first = name.split()[0]
        if len(first) >= 3 and re.search(re.escape(first), body, flags=re.I):
            last_seen[name] = chapter_number
    book.patch_state(last_seen=last_seen, characters=chars)


def refresh_facts(book: BookProject, chapter_number: int, chapter_text: str) -> str:
    old = book.read_text("facts.md")
    result = complete_for(
        "facts",
        [
            {
                "role": "system",
                "content": (
                    "Обнови краткий список фактов романа (20–40 строк): деньги, раны, где кто, "
                    "день/сезон, обязательства. Русский. Без прозы. Сохрани старое, что не изменилось. "
                    "Формат: `- факт`."
                ),
            },
            {
                "role": "user",
                "content": pack(
                    f"Глава {chapter_number}:",
                    chapter_text[:12000],
                    "Старые факты:",
                    old or "(пусто)",
                    "Таймлайн:",
                    book.read_text("timeline.md")[:2000],
                ),
            },
        ],
        max_tokens=1200,
        temperature=0.2,
    )
    if not result.ok:
        return old
    book.write_text("facts.md", result.text)
    return result.text


def append_timeline(book: BookProject, chapter_number: int, chapter_text: str) -> None:
    result = complete_for(
        "facts",
        [
            {
                "role": "system",
                "content": "Одна строка таймлайна: день/время/место якорем. Без сюжета целиком. Русский.",
            },
            {
                "role": "user",
                "content": pack(
                    f"Глава {chapter_number}, выжимка:",
                    chapter_text[:4000],
                    "Уже есть:",
                    book.read_text("timeline.md")[-1500:],
                ),
            },
        ],
        max_tokens=200,
        temperature=0.2,
    )
    if not result.ok:
        return
    prev = book.read_text("timeline.md").rstrip()
    line = result.text.strip().splitlines()[0]
    book.write_text("timeline.md", (prev + "\n" + f"- Глава {chapter_number}: {line}").strip() + "\n")


def update_summary(book: BookProject, chapter_number: int, chapter_text: str) -> None:
    old = book.read_state().get("summary") or ""
    result = complete_for(
        "facts",
        [
            {
                "role": "system",
                "content": "Обнови краткое саммари сюжета (до 400 слов). Русский. Только факты событий.",
            },
            {
                "role": "user",
                "content": pack(
                    "Старое саммари:",
                    old or "(начало книги)",
                    f"Новая глава {chapter_number}:",
                    chapter_text[:8000],
                ),
            },
        ],
        max_tokens=800,
        temperature=0.3,
    )
    if result.ok:
        book.patch_state(summary=result.text)
