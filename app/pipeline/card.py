from __future__ import annotations

from app.llm.router import complete_with_continue
from app.pipeline.prompts import CANON_RULE, HUMAN_PROSE, comment_block, pack
from app.storage.project import BookProject


SYSTEM = f"""Ты редактор и постановщик романа. Язык — только русский.
{HUMAN_PROSE}
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
    _sync_flags(book, result.text)
    return result.text


def _sync_flags(book: BookProject, text: str) -> None:
    low = text.lower()
    is_original = "оригинал" in low and "вселенн" not in low[:400]
    if "вселенн" in low:
        is_original = False
    canon = "au" not in low and "альтернативн" not in low
    if "канон" in low:
        canon = True
    if " au" in f" {low}" or "а.у." in low or "альтарнетив" in low:
        canon = False
    adult = any(x in low for x in ("18+", "18 +", "эрот", "жестокость", "nsfw"))
    book.patch_state(is_original=is_original, canon=canon, adult=adult)
