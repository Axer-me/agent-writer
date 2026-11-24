"""Лист драмы: чего герой хочет, во что верит, чем платит и кем становится.

Пишется до плана на 50 глав. У авторской книги — ещё и до библии персонажей.
"""

from __future__ import annotations

import re

from app.llm.router import complete_with_continue
from app.pipeline.prompts import CANON_RULE, comment_block, pack_priority
from app.pipeline.scale import CHAPTER_CAP, book_band
from app.storage.project import BookProject

_FIELDS = (
    ("hero", "Герой"),
    ("want", "Хочет"),
    ("belief", "Верит"),
    ("cost", "Цена"),
    ("becomes", "Становится"),
    ("act1", "Акт 1"),
    ("act2", "Акт 2"),
    ("act3", "Акт 3"),
    ("world1", "Мир акта 1"),
    ("world2", "Мир акта 2"),
    ("world3", "Мир акта 3"),
)

_READY = ("want", "belief", "cost", "becomes", "act1", "act2", "act3")

SYSTEM = f"""Ты составляешь лист драмы романа. Русский. Без прозы и без плана по главам.
{CANON_RULE}
Лист отвечает, как меняется герой и как по актам открывается мир. Это не перечень событий и не проза.
Ровно одиннадцать полей, с этими подписями и без других подписей.
Под каждой подписью — связный абзац в 3–5 предложений. Следующие строки без новой подписи относятся к тому же полю.
Герой: имя и кем он приходится истории
Хочет: цель на всю книгу, почему она нужна сейчас, что будет, если герой её не получит
Верит: формулировка убеждения, как оно выглядит в поступке, чего герой из-за него не видит
Цена: конкретная потеря, кто её приносит, что герой пока считает приемлемым
Становится: какой выбор в финале, чем этот поступок расходится с прежней верой
Акт 1: главы A–B, что давит, какую часть веры герой ещё защищает, чем положение в конце акта отличается от начала
Акт 2: главы C–D, старый способ не даёт желаемого, что трескается, чем кончается акт
Акт 3: главы E–F, выбор, который прежняя вера не покрывает, чем положение в конце отличается от начала акта
Мир акта 1: те же главы, что у акта 1. Какой стороной мир открывается читателю в начале: человек, место, время, обычный день и одно правило этой жизни. Тайна книги сюда не входит
Мир акта 2: те же главы, что у акта 2. Какая новая сторона мира становится видна, когда первое место или обычная жизнь уже не держат героя
Мир акта 3: те же главы, что у акта 3. Каким читатель видит мир к финалу и что по-прежнему не объясняется вслух
Поля «Мир акта» не повторяют веру героя. Они говорят, что читатель успевает увидеть вокруг.
Три отрезка вместе закрывают главы 1–N, без дыр и без наложений.
N — число глав из запроса. Длину акта выбирай по этой истории. Не копируй чужие номера глав.
Не раскрывай тайну в акте 1. Не пересказывай запрет карточки («не открывает», «не выходит»).
Образец показывает только подписи полей. Имя Марек, ремень и номера глав из образца в книгу не переноси:
Герой:
Хочет:
Верит:
Цена:
Становится:
Акт 1:
Акт 2:
Акт 3:
Мир акта 1:
Мир акта 2:
Мир акта 3:
"""

_ORIGINAL = (
    "\nЭто оригинал. Линия и мир берутся из карточки. "
    "Мира ещё нет: собери его открытие по актам, не переноси известный чужой мир. "
    "Не добавляй вторую тайну и не меняй место, с которого начинается замысел.\n"
)

_FOREIGN = (
    "\nЭто чужая вселенная. Линия — только этой книги. "
    "Поля «Мир акта» берут только карточку и заметки. Факта, которого там нет, не добавляй. "
    "Если заполнить нечем, напиши: в заметках этого нет. "
    "Не переписывай биографию, которой нет в карточке и в заметках. "
    "Если библии ещё нет, не выдумывай канон: оставь перемену внутри того, что уже сказано.\n"
)


def empty_drama() -> dict[str, str]:
    return {key: "" for key, _label in _FIELDS}


def parse_drama(text: str) -> dict[str, str]:
    found = empty_drama()
    chunks = {key: [] for key, _label in _FIELDS}
    current = ""
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-*•").strip()
        if not line:
            continue
        key = ""
        value = ""
        if ":" in line:
            label, rest = line.split(":", 1)
            key = _key_for(label.strip().lower())
            value = rest.strip()
        if key:
            current = key
            if value:
                chunks[key].append(value)
            continue
        if current:
            chunks[current].append(line)
    for key, _label in _FIELDS:
        found[key] = "\n".join(chunks[key]).strip()
    return found


def drama_ready(drama: dict[str, str] | None) -> bool:
    if not drama:
        return False
    return all((drama.get(key) or "").strip() for key in _READY)


def render_drama(drama: dict[str, str] | None) -> str:
    data = drama or empty_drama()
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


_RANGE = re.compile(r"(\d+)\s*[–—-]\s*(\d+)")


def act_spans(drama: dict[str, str] | None) -> list[tuple[int, int]] | None:
    """Отрезки глав из листа. Пусто, если лист не готов или акт не назвал номера."""
    if not drama_ready(drama):
        return None
    spans: list[tuple[int, int]] = []
    for key in ("act1", "act2", "act3"):
        match = _RANGE.search((drama or {}).get(key) or "")
        if not match:
            return None
        start, end = int(match.group(1)), int(match.group(2))
        if not 1 <= start <= end <= CHAPTER_CAP:
            return None
        spans.append((start, end))
    return spans


def chapter_drama(drama: dict[str, str] | None, number: int, total: int | None = None) -> str:
    """Один ход главы словами листа. Без анкеты «вера уже стоит»."""
    if not drama_ready(drama):
        return ""
    data = drama or empty_drama()
    number = max(1, min(CHAPTER_CAP, int(number)))
    hero = data["hero"] or "Герой"
    want, belief, cost, becomes = data["want"], data["belief"], data["cost"], data["becomes"]
    index, phase = _drama_band(data, number, total)
    act = data[f"act{index + 1}"]
    if index == 0:
        steps = (
            f"{hero} добивается: {want}. Поступает так, потому что убеждён: {belief}. {act}",
            f"{hero} уже платит: {cost}, и всё ещё верит: {belief}. Эту цену пока считает нормальной.",
            f"Цена {cost} уже на виду. {hero} не отказывается от убеждения: {belief}.",
        )
    elif index == 1:
        steps = (
            f"Прежний способ не приносит желаемого: {want}. {act}",
            f"{hero} делает по-старому и снова платит: {cost}.",
            f"Убеждение дало трещину. Стать «{becomes}» уже можно, и {hero} ещё не выбирает это.",
        )
    else:
        steps = (
            f"Дальнейший выбор не объясняется убеждением: {belief}. {act}",
            f"Цена {cost} — плата за то, чтобы стать: {becomes}.",
            f"{hero} становится: {becomes}. Старое убеждение этот поступок не покрывает.",
        )
    return steps[phase]


def world_act(drama: dict[str, str] | None, number: int) -> str:
    """Абзац о мире того акта, в котором стоит глава. Пустое поле ничего не добавляет."""
    data = drama or empty_drama()
    if not any((data.get(key) or "").strip() for key in ("world1", "world2", "world3")):
        return ""
    index, _phase = _drama_band(data, number)
    return (data.get(f"world{index + 1}") or "").strip()


def _drama_band(data: dict[str, str], number: int, total: int | None = None) -> tuple[int, int]:
    """Без номеров в листе книга не делится на 1–15, 16–35 и 36–50."""
    spans = act_spans(data)
    if spans:
        for index, (start, end) in enumerate(spans):
            if start <= number <= end:
                width = max(1, end - start + 1)
                return index, min(2, (number - start) * 3 // width)
        start, end = spans[-1]
        width = max(1, end - start + 1)
        return len(spans) - 1, min(2, (number - start) * 3 // width)
    if total and total >= 3:
        third = max(1, total // 3)
        if number <= third:
            start, end, index = 1, third, 0
        elif number <= third * 2:
            start, end, index = third + 1, third * 2, 1
        else:
            start, end, index = third * 2 + 1, total, 2
        width = max(1, end - start + 1)
        return index, min(2, (number - start) * 3 // width)
    return 0, min(2, (number - 1) % 3)


def generate_drama(book: BookProject, comment: str | None = None) -> str:
    original = bool(book.read_state().get("is_original", True))
    store = _store(book)
    label, low, high = book_band(book)
    system = (
        SYSTEM
        + (_ORIGINAL if original else _FOREIGN)
        + f"\nРазмер «{label}». Выбери N от {low} до {high} включительно: столько глав, сколько нужно сюжету.\n"
    )
    result = complete_with_continue(
        "drama",
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": pack_priority(
                    [
                        "Карточка:\n" + (book.read_text("card.md") or "(пусто)"),
                        comment_block(comment),
                        "Текущий лист, если его надо поправить:\n"
                        + (book.read_text("drama.md") or "(пусто — составь с нуля)"),
                    ],
                    [
                        "Библия:\n" + (book.read_text("bible.md")[:4000] or "(ещё нет)"),
                        "Заметки канона:\n" + (store.canon_text() or "(нет)"),
                    ]
                    if not original
                    else [],
                ),
            },
        ],
        max_tokens=1800,
        temperature=0.4,
        max_rounds=2,
    )
    if not result.ok:
        raise RuntimeError(result.error or "Не удалось составить лист драмы")
    parsed = parse_drama(result.text)
    if not drama_ready(parsed):
        raise RuntimeError("Лист драмы пришёл без линии героя. Перепишите его.")
    text = render_drama(parsed)
    store.save_drama(parsed)
    book.write_text("drama.md", text + "\n")
    _invalidate(book, "drama")
    return text


def ensure_drama(book: BookProject) -> str:
    """Пустой лист пишется сам. Уже готовый лист повторный вызов не тратит."""
    store = _store(book)
    current = store.drama()
    if drama_ready(current):
        return render_drama(current)
    return generate_drama(book, None)


def _key_for(label: str) -> str:
    for key, name in _FIELDS:
        if label == name.lower():
            return key
    return ""


def _store(book: BookProject):
    from app.pipeline.plan import _store as plan_store

    return plan_store(book)


def _invalidate(book: BookProject, source: str) -> None:
    from app.pipeline.approvals import stale_after

    state = book.read_state()
    stale = stale_after(
        state.get("stale") or {},
        source,
        is_original=bool(state.get("is_original", True)),
    )
    book.patch_state(stale=stale)
