"""Лист мира и строка «что узнаёт читатель» у каждой главы.

Своя книга придумывает мир. Чужая только раскладывает уже записанные заметки.
"""

from __future__ import annotations

import re

from app.llm.router import complete_with_continue
from app.pipeline.prompts import CANON_RULE, comment_block, pack_priority
from app.storage.project import BookProject

_FIELDS = (
    ("when_where", "Когда и где"),
    ("daily", "Обычная жизнь"),
    ("first_hour", "Первый час"),
    ("hidden", "Пока скрыто"),
)
_REVEAL = re.compile(r"(?i)^(?:##\s*)?Глава\s+(\d+)\b(.*)$")
_REVEAL_LINE = re.compile(r"(?i)^читатель узнаёт\s*:\s*(.*)$")


def empty_world() -> dict[str, str]:
    return {key: "" for key, _label in _FIELDS}


def parse_world(text: str) -> tuple[dict[str, str], dict[int, str]]:
    """Четыре поля листа и по одной строке на главу. Продолжение без подписи остаётся в том же поле."""
    sheet = empty_world()
    chunks = {key: [] for key, _label in _FIELDS}
    reveals: dict[int, list[str]] = {}
    current = ""
    chapter = 0
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-*•").strip()
        if not line or line.lower().startswith("глав:"):
            continue
        heading = _chapter_heading(line)
        if heading:
            chapter = heading
            current = ""
            reveals.setdefault(chapter, [])
            continue
        reveal = _REVEAL_LINE.match(line)
        if reveal and chapter:
            current = ""
            if reveal.group(1).strip():
                reveals.setdefault(chapter, []).append(reveal.group(1).strip())
            continue
        key = ""
        value = ""
        if ":" in line and not chapter:
            label, rest = line.split(":", 1)
            key = _key_for(label.strip().lower())
            value = rest.strip()
        if key:
            current = key
            chapter = 0
            if value:
                chunks[key].append(value.lstrip("*").strip())
            continue
        if chapter and line:
            reveals.setdefault(chapter, []).append(line)
            continue
        if current:
            chunks[current].append(line.lstrip("*").strip())
    for key, _label in _FIELDS:
        sheet[key] = "\n".join(chunks[key]).strip()
    return sheet, {number: " ".join(parts).strip() for number, parts in reveals.items() if parts}


def render_world(world: dict[str, str] | None) -> str:
    data = world or empty_world()
    parts: list[str] = []
    for key, label in _FIELDS:
        value = (data.get(key) or "").strip()
        if not value:
            parts.append(f"{label}:")
            continue
        lines = value.splitlines()
        parts.append(f"{label}: {lines[0].strip()}".rstrip())
        parts.extend(line.rstrip() for line in lines[1:])
    return "\n".join(parts)


def world_system(original: bool) -> str:
    if original:
        source = (
            "Это своя книга. Мира ещё нет: собери его из карточки, развития сюжета и плана. "
            "Не переноси известный чужой мир."
        )
    else:
        source = (
            "Чужой мир уже существует. Бери только заметки и карточку. "
            "Факта, которого там нет, не добавляй и не бери из памяти о чужой истории. "
            "Если поля нечем заполнить, напиши: в заметках этого нет."
        )
    return f"""Составь лист мира и подробный абзац на каждую главу плана. Русский. Без прозы главы.
{CANON_RULE}
{source}
Четыре поля. У каждого несколько предложений. Следующие строки без новой подписи относятся к тому же полю:
Когда и где: эпоха, место, время суток или года, в которых живёт герой
Обычная жизнь: день, работа, деньги, опасность и обычай, которые герой считает нормой
Первый час: что заметно в первый час рядом с героем — одежда, запах, звук, правило
Пока скрыто: что читатель пока не должен понять. Это не повод молчать о месте, времени и человеке
Поля «Мир акта» в развитии сюжета уже задают, какая сторона мира открывается в каждом акте. Абзац главы берёт сторону своего акта и не пересказывает весь лист.
Затем для каждой главы плана:
## Глава N
Читатель узнаёт: несколько предложений, тот же объём, что у события этой главы. Не одна фраза и не справка на всю книгу.
Где читатель находится в этой главе: место и время именно здесь.
Что видно вокруг: предмет, одежда, звук, запах, привычка или правило, по которым это место узнаётся.
Какая сторона мира здесь становится ясна. Бери её из поля «Мир акта» того отрезка, куда попадает глава.
Чего читатель по-прежнему не понимает. Тайну из «Пока скрыто» и из развития сюжета не открывай.
Глава 1 представляет человека, место, время, обычный день и одно правило этой жизни до того, как сюжет столкнёт героя.
Со второй главы не повторяй это знакомство. Если место то же, не описывай его заново: добавь одну новую сторону мира. Если герой перешёл в другое место, опиши новое место так же подробно, как первое.
Соседние главы не открывают одну и ту же сторону мира.
"""


def fill_world(book: BookProject, comment: str | None = None) -> None:
    """Отдельный вызов после плана. Пустой ответ не затирает уже записанный лист."""
    from app.pipeline.plan import _store

    from app.pipeline.drama import render_drama

    store = _store(book)
    chapters = store.plan_chapters()
    if not chapters:
        return
    state = book.read_state()
    original = bool(state.get("is_original", True))
    brief = "\n".join(
        f"Глава {row['number']}. {row.get('title') or ''} — {(row.get('event') or '')[:240]}"
        for row in chapters
    )
    result = complete_with_continue(
        "plan",
        [
            {"role": "system", "content": world_system(original)},
            {
                "role": "user",
                "content": pack_priority(
                    [
                        comment_block(comment),
                        "Главы, для которых нужна строка:\n" + brief,
                        "Развитие сюжета, в том числе план мира по актам:\n"
                        + (render_drama(store.drama()) or "(листа ещё нет)"),
                        "Заметки о мире:\n" + (store.canon_text() or "(заметок нет)"),
                    ],
                    [
                        "Карточка:\n" + book.read_text("card.md")[:4000],
                        "Персонажи:\n" + book.read_text("bible.md")[:4000],
                    ],
                ),
            },
        ],
        max_tokens=4000,
        temperature=0.4,
        max_rounds=3,
    )
    if not result.ok:
        raise RuntimeError(result.error or "Не удалось собрать лист мира")
    sheet, reveals = parse_world(result.text)
    if any(sheet.values()):
        store.save_world(sheet)
    known = {int(row["number"]) for row in chapters}
    store.save_reveals({number: text for number, text in reveals.items() if number in known and text})


def _chapter_heading(line: str) -> int:
    match = _REVEAL.match(line)
    if not match or ":" in line:
        return 0
    tail = match.group(2).strip().lstrip(".").strip()
    if len(tail.split()) > 8:
        return 0
    return int(match.group(1))


def _key_for(label: str) -> str:
    folded = label.replace("ё", "е")
    for key, name in _FIELDS:
        if folded == name.lower().replace("ё", "е"):
            return key
    return ""
