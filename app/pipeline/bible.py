from __future__ import annotations

import re

from app.llm.router import complete_with_continue
from app.pipeline.prompts import CANON_RULE, comment_block, pack_priority
from app.storage.project import BookProject


SYSTEM = f"""Ты составляешь библию персонажей для романа на русском.
{CANON_RULE}
Нужны ВСЕ главные и второстепенные, кто важен для сюжета этой книги.
Формат markdown. Заголовок человека — ## Имя. Без предисловий и без людей, которым нечего делать в этой истории.
Под каждым именем одни и те же подписи. Кроме статуса, под каждой подписью 2–4 предложения:
Внешность и манера: одна узнаваемая деталь, не жест, который придётся повторять в каждом абзаце. Вещи, лекарства и оружие только из эпохи листа мира и карточки
Хочет от героя: чего добивается именно от главного героя
Давит на веру: поддерживает убеждение героя или ломает его, и каким поступком
Отношение: к кому в этой книге относится так, что это меняет сцену. Если такого нет, напиши «не меняет сцену»
Статус: жив, мёртв или ушёл
"""


def generate_bible(book: BookProject, comment: str | None = None) -> str:
    from app.pipeline.drama import drama_ready, ensure_drama, render_drama
    from app.pipeline.plan import _store

    original = bool(book.read_state().get("is_original", True))
    if original:
        ensure_drama(book)
    store = _store(book)
    notes = "" if original else store.canon_text()
    drama = render_drama(store.drama()) if drama_ready(store.drama()) else ""
    system = SYSTEM
    if original and drama:
        system += (
            "\nДрама героя уже задана. Не придумывай другую линию изменения. "
            "Каждый человек нужен как давление на эту линию: чего он хочет от героя "
            "и какую веру поддерживает или ломает. "
            "Не добавляй людей, которым нечего делать с этой переменой.\n"
        )
    elif not original:
        system += (
            "\nЭто чужая вселенная. Пиши только то, что прямо сказано в карточке и в подтверждённых заметках. "
            "Если факта там нет, поставь статус «не подтверждено» и не добавляй биографию из памяти модели.\n"
        )
    result = complete_with_continue(
        "bible",
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": pack_priority(
                    [
                        "Карточка:\n" + (book.read_text("card.md") or "(пусто)"),
                        "Лист драмы:\n" + (drama or "(для этой книги ещё не задан)"),
                        comment_block(comment),
                    ],
                    [
                        "Подтверждённые заметки канона:\n"
                        + (notes or "(нет — для чужой вселенной не выдумывай канон)"),
                        "Стиль:\n" + (book.read_text("style.md")[:3000] or "(нет)"),
                        "Текущая библия:\n" + (book.read_text("bible.md") or "(пусто — составь с нуля)"),
                    ],
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
        from app.pipeline.select import person_tokens

        call = (person_tokens(name)[:1] or [name])[0]
        found.append({"name": name, "status": status, "call": call})
    return found
