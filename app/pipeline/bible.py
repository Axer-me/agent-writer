from __future__ import annotations

import re

from app.llm.router import complete_with_continue
from app.pipeline.prompts import CANON_RULE, comment_block, pack
from app.storage.project import BookProject


SYSTEM = f"""Ты составляешь библию персонажей для романа на русском.
{CANON_RULE}
Нужны ВСЕ главные и второстепенные, кто важен для сюжета вселенной или этой книги.
Для каждого:
- имя
- краткое описание внешности/манеры (без воды)
- роль в основном сюжете вселенной (или в этой книге, если оригинал)
- статус: жив / мёртв / ушёл
Формат markdown. Заголовки ## Имя. Без предисловий.
"""


def generate_bible(book: BookProject, comment: str | None = None) -> str:
    result = complete_with_continue(
        "bible",
        [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": pack(
                    "Карточка:",
                    book.read_text("card.md") or "(пусто)",
                    "Стиль:",
                    book.read_text("style.md")[:3000] or "(нет)",
                    "Текущая библия:",
                    book.read_text("bible.md") or "(пусто — составь с нуля)",
                    comment_block(comment),
                ),
            },
        ],
        max_tokens=4000,
        temperature=0.5,
        max_rounds=4,
    )
    if not result.ok:
        raise RuntimeError(result.error or "Не удалось составить библию")
    book.write_text("bible.md", result.text)
    chars = parse_characters(result.text)
    state = book.read_state()
    last_seen = state.get("last_seen") or {}
    for ch in chars:
        last_seen.setdefault(ch["name"], 0)
    book.patch_state(characters=chars, last_seen=last_seen)
    return result.text


def parse_characters(markdown: str) -> list[dict]:
    found: list[dict] = []
    for match in re.finditer(r"^##\s+(.+)$", markdown, flags=re.M):
        name = match.group(1).strip().strip("*")
        start = match.end()
        nxt = re.search(r"^##\s+", markdown[start:], flags=re.M)
        end = start + nxt.start() if nxt else None
        block = markdown[start:end]
        low = block.lower()
        status = "alive"
        if re.search(r"статус:.*мерт", low) or "мёртв" in low or "мертв" in low:
            status = "dead"
        elif re.search(r"статус:.*уш", low) or "ушёл" in low or "ушел" in low:
            status = "gone"
        found.append({"name": name, "status": status})
    return found
