from __future__ import annotations

from app.llm.router import complete_with_continue
from app.pipeline.prompts import CANON_RULE, comment_block, pack
from app.storage.project import BookProject


SYSTEM = f"""Ты составляешь план романа на 50 глав, на русском.
{CANON_RULE}
План должен следовать синопсису из карточки.

На ОДНУ главу — 1–2 крупных хода, не весь акт и не вся завязка романа.
«Кратко» описывает только то, что успевает случиться В ЭТОЙ главе, и одной фразой — что сознательно откладывается.
Раскрытие личности, титула, тайны крови, «клятва на всю жизнь» — не в главе 1, если синопсис можно растянуть.
Глава 1: ситуация героя (уже в пути / после какого дела), место, первое касание сюжета. Без полного бэкстори второго героя и без финала знакомства.

Для каждой главы:
## Глава N. Название
Теги: 2–4 метки сцен через запятую (например: засада, торг, дорога, разговор)
Кратко: 4–8 предложений событий этой главы + что ещё рано.
Не повторяй один и тот же ход слишком часто. Без предисловий до главы 1.
"""


def generate_plan(book: BookProject, comment: str | None = None) -> str:
    result = complete_with_continue(
        "plan",
        [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": pack(
                    "Карточка:",
                    book.read_text("card.md"),
                    "Библия (сжать внимание на арки):",
                    book.read_text("bible.md")[:8000],
                    "Стиль:",
                    book.read_text("style.md")[:2000],
                    "Текущий план:",
                    book.read_text("plan.md") or "(пусто — все 50 глав)",
                    comment_block(comment),
                    "Нужны главы 1–50. Если уже есть обрезок — дополни недостающие, не ломая утверждённую логику без комментария.",
                ),
            },
        ],
        max_tokens=4000,
        temperature=0.65,
        max_rounds=5,
    )
    if not result.ok:
        raise RuntimeError(result.error or "Не удалось составить план")
    book.write_text("plan.md", result.text)
    return result.text
