from __future__ import annotations

import re

from app.llm.router import complete_with_continue
from app.pipeline.prompts import CANON_RULE, comment_block, pack
from app.storage.project import BookProject


SYSTEM = f"""Ты редактор и постановщик романа. Язык — только русский.
Это карточка замысла, не проза главы. Пиши разделы коротко и по делу.
{CANON_RULE}
Верни markdown-карточку произведения, без предисловий.
Секции:
# Название
# Оригинал или вселенная (если вселенная — какая)
# Канон или AU
# Жанр
# POV и время глаголов
# Синопсис
# Тон и рейтинг (можно 18+)
# Ограничения пользователя
Если пользователь что-то уже задал — не ломай, дополни остальное.
Если не задал — предложи конкретные решения, не список вариантов на выбор.
"""


def generate_card(book: BookProject, comment: str | None = None) -> str:
    existing = book.read_text("card.md")
    style = book.read_text("style.md")
    user = pack(
        f"Название папки/книги: {book.read_state().get('title')}",
        "Текущая карточка (может быть пустой или черновиком):",
        existing or "(пусто — предложи целиком)",
        "Стиль, если уже есть:",
        style[:4000] if style else "(нет)",
        comment_block(comment),
    )
    result = complete_with_continue(
        "card",
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
        max_tokens=2500,
        temperature=0.7,
    )
    if not result.ok:
        raise RuntimeError(result.error or "Не удалось составить карточку")
    book.write_text("card.md", result.text)
    sync_card_flags(book, result.text)
    return result.text


_HEADING = re.compile(r"(?m)^(#{1,6})\s+(.+)$")


def sync_card_flags(book: BookProject, text: str) -> None:
    book.patch_state(**infer_card_flags(text, book.read_state()))


def infer_card_flags(text: str, previous: dict | None = None) -> dict:
    """Флаги читаются из текста разделов. Слова в заголовках сами по себе не выбор пользователя."""
    previous = previous or {}
    universe = _section_body(text, "вселен", "оригинал").lower()
    canon_body = _section_body(text, "канон", "au").lower()
    rating = _section_body(text, "рейтинг", "тон").lower()

    if universe.strip():
        mentions_universe = re.search(r"вселенн", universe) is not None
        says_original = "оригинал" in universe
        if mentions_universe and says_original and re.search(r"не\s+вселенн", universe):
            is_original = True
        elif mentions_universe:
            is_original = False
        else:
            is_original = says_original or bool(previous.get("is_original", True))
    else:
        is_original = bool(previous.get("is_original", True))

    if canon_body.strip():
        au = re.search(r"(?<![\w-])au(?![\w-])|альтернатив", canon_body) is not None
        negated_au = re.search(r"не\s+au|без\s+au|не\s+альтернатив", canon_body) is not None
        if au and not negated_au:
            canon = False
        elif "канон" in canon_body or negated_au:
            canon = True
        else:
            canon = bool(previous.get("canon", True))
    else:
        canon = bool(previous.get("canon", True))

    if rating.strip():
        negated = re.search(r"нет\s*18|без\s*18|не\s*18|pg-?\s*13|семейн", rating) is not None
        explicit = re.search(r"18\s*\+|эрот|nsfw|жестокост", rating) is not None
        adult = explicit and not negated
    else:
        adult = bool(previous.get("adult", False))

    return {"is_original": is_original, "canon": canon, "adult": adult}


def _section_body(text: str, *needles: str) -> str:
    matches = list(_HEADING.finditer(text or ""))
    chunks: list[str] = []
    for index, match in enumerate(matches):
        title = match.group(2).strip().lower()
        if not any(needle in title for needle in needles):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunks.append(text[match.end() : end])
    return "\n".join(chunks)
