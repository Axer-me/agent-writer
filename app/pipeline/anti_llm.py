from __future__ import annotations

from app.llm.router import complete_for
from app.pipeline.prompts import BIBLE_NAMES, HUMAN_PROSE, KEEP_LENGTH, pack, word_count

KEEP_RATIO = 0.90


def de_llm(prose: str, style_md: str = "") -> str:
    result = complete_for(
        "anti_llm",
        [
            {
                "role": "system",
                "content": f"""Перепиши главу так, чтобы она звучала как живой автор, не нейросеть.
{HUMAN_PROSE}
{KEEP_LENGTH}
{BIBLE_NAMES}
Сохрани сюжет, факты, имена, порядок сцен и почти весь объём.
Не превращай в конспект. Не выкидывай затравку, дорогу и паузы.
Если дан образец стиля — следуй ему.
Верни только текст главы той же полноты.""",
            },
            {
                "role": "user",
                "content": pack(
                    "Стиль:",
                    style_md[:3500] or "(нет образца — всё равно убери нейросетевой налёт)",
                    "Глава (не укорачивай):",
                    prose,
                ),
            },
        ],
        max_tokens=4000,
        temperature=0.75,
    )
    if not result.ok:
        return prose
    out = result.text or prose
    if word_count(out) < int(word_count(prose) * KEEP_RATIO):
        return prose
    return out
