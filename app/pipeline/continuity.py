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
                        f"{name}: по записям жив, ухода нет, не появляется {gap} глав. "
                        f"Можно вернуть в ближайшие {REENGAGE_WINDOW} глав или объяснить, почему персонажа нет. "
                        "Само по себе ничего не изменится."
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
    from app.pipeline.select import mentioned

    for ch in chars:
        name = ch.get("name") or ""
        if name and mentioned(body, name, ch.get("call") or ""):
            last_seen[name] = chapter_number
    book.patch_state(last_seen=last_seen, characters=chars)


def chapter_for_memory(text: str, limit: int) -> str:
    """Начало и финал главы. Середина сокращается первой, итог остаётся."""
    text = text or ""
    if len(text) <= limit:
        return text
    marker = "\n\n[...середина главы пропущена, ниже финал...]\n\n"
    if limit <= len(marker) + 80:
        return text[-limit:]
    tail = max(80, (limit - len(marker)) // 2)
    head = limit - len(marker) - tail
    return text[:head] + marker + text[-tail:]


def refresh_facts(book: BookProject, chapter_number: int, chapter_text: str) -> str:
    from app.pipeline.plan import _store
    from app.storage.story import fact_lines, usable_facts

    store = _store(book)
    old = store.facts_text() or book.read_text("facts.md")
    result = complete_for(
        "facts",
        [
            {
                "role": "system",
                "content": (
                    "Обнови короткий список фактов. Русский. Без прозы. "
                    "Не больше шести строк о том, что изменилось в этой сцене. "
                    "Старые строки, которые всё ещё верны, повтори. "
                    "Не пиши того, чего в тексте не было: никаких «нет упоминания» и пустых граф. "
                    "Каждый факт с новой строки в виде `- факт`."
                ),
            },
            {
                "role": "user",
                "content": pack(
                    f"Глава {chapter_number}:",
                    chapter_for_memory(chapter_text, 12000),
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
    parsed = usable_facts(fact_lines(result.text))
    if parsed:
        store.replace_facts(parsed, chapter_number)
        return store.facts_text()
    return old


def append_timeline(book: BookProject, chapter_number: int, chapter_text: str) -> None:
    result = complete_for(
        "facts",
        [
            {
                "role": "system",
                "content": (
                    "Одна строка таймлайна: день/время/место якорем. Без сюжета целиком. Русский. "
                    "Опирайся на финал главы, не только на начало."
                ),
            },
            {
                "role": "user",
                "content": pack(
                    f"Глава {chapter_number}, выжимка:",
                    chapter_for_memory(chapter_text, 4000),
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
                "content": (
                    "Обнови краткое саммари сюжета (до 400 слов). Русский. Только факты событий. "
                    "Обязательно включи итог главы: решение, рану, уход или раскрытие в конце текста."
                ),
            },
            {
                "role": "user",
                "content": pack(
                    "Старое саммари:",
                    old or "(начало книги)",
                    f"Новая глава {chapter_number}:",
                    chapter_for_memory(chapter_text, 8000),
                ),
            },
        ],
        max_tokens=800,
        temperature=0.3,
    )
    if result.ok:
        book.patch_state(summary=result.text)


def refresh_events(book: BookProject, chapter_number: int, chapter_text: str) -> str:
    from app.pipeline.plan import _store
    from app.storage.story import parse_event_updates

    store = _store(book)
    old = store.events_text() or "(открытых событий нет)"
    result = complete_for(
        "facts",
        [
            {
                "role": "system",
                "content": (
                    "Выпиши только новые и изменившиеся события этой главы. Русский. Без прозы. "
                    "Тип только: решение, рана, уход, знание. "
                    "Закрывай старое событие только строкой той же главы и того же типа со статусом «закрыто». "
                    "Формат каждой строки: `- Глава N | тип | что случилось | открыто` "
                    "или `| закрыто`. Не повторяй события, которые не изменились."
                ),
            },
            {
                "role": "user",
                "content": pack(
                    f"Глава {chapter_number}, финал важнее начала:",
                    chapter_for_memory(chapter_text, 8000),
                    "Уже открытые события:",
                    old,
                ),
            },
        ],
        max_tokens=800,
        temperature=0.2,
    )
    if not result.ok:
        return store.events_text()
    updates = parse_event_updates(result.text)
    if not updates:
        return store.events_text()
    store.apply_events(updates)
    return store.events_text()


def resolve_arc(parsed: dict | None, chapters: list[dict], start: int, end: int, drama: dict | None = None) -> dict:
    """Пустой ответ модели не оставляет окно пустым. Уже разобранное обещание не затирается."""
    from app.pipeline.scale import CHAPTER_CAP

    end = min(CHAPTER_CAP, end)
    if parsed and (parsed.get("promise") or "").strip():
        filled = fill_arc_from_plan(chapters, parsed, start, end)
    else:
        base = parsed or {
            "promise": "",
            "payoff_chapter": 0,
            "start_chapter": start,
            "end_chapter": end,
            "steps": "",
        }
        filled = fill_arc_from_plan(chapters, base, start, end)
        if not (filled.get("promise") or "").strip():
            filled["promise"] = "Раскрытие этой линии ещё рано."
        if not int(filled.get("payoff_chapter") or 0):
            filled["payoff_chapter"] = min(CHAPTER_CAP, end)
    return sharpen_arc(filled, chapters, drama)


def refresh_arc(book: BookProject, chapter_number: int) -> str:
    from app.pipeline.plan import _store

    store = _store(book)
    from app.pipeline.scale import CHAPTER_CAP

    end = min(CHAPTER_CAP, chapter_number + 4)
    result = complete_for(
        "plan",
        [
            {
                "role": "system",
                "content": (
                    "Окно арки на ближайшие главы. Русский. Без прозы. "
                    "Не переписывай весь план.\n"
                    "Секрет из обещания не раскрывай раньше выплаты. "
                    "Каждая глава всё равно меняет положение героя.\n"
                    "Образец формата, имена из образца не переноси:\n"
                    "Обещание: кто оставил вещь, ещё рано\n"
                    "Выплата не раньше главы: 6\n"
                    "Глава 2: утром приходит второй человек, из-за закрытой тайны герой ошибается в деле\n"
                    "Глава 3: ошибку замечают, герой платит доверием, тайну не раскрывает\n"
                    "Глава 4: герой прячет тайну с глаз и этим создаёт новую помеху\n"
                    "Запрещено писать шагом только «не открывает», «продолжает то же дело» или «игнорирует»."
                ),
            },
            {
                "role": "user",
                "content": pack(
                    f"Нужны шаги глав {chapter_number}–{end}.",
                    "Карточка:\n" + book.read_text("card.md")[:2500],
                    "План этих глав:\n" + _window_plan(store, chapter_number, end),
                    "Открытые события:\n" + (store.events_text() or "(нет)"),
                    "Прежнее окно:\n" + (store.arc_text() or "(нет)"),
                ),
            },
        ],
        max_tokens=700,
        temperature=0.4,
    )
    if not result.ok:
        existing = store.arc() or {}
        if (existing.get("promise") or "").strip():
            store.save_arc(sharpen_arc(existing, store.plan_chapters(), store.drama()))
            return store.arc_text()
        store.save_arc(resolve_arc(None, store.plan_chapters(), chapter_number, end, store.drama()))
        return store.arc_text()
    arc = resolve_arc(
        _parse_arc(result.text, chapter_number, end),
        store.plan_chapters(),
        chapter_number,
        end,
        store.drama(),
    )
    store.save_arc(arc)
    return store.arc_text()


def _window_plan(store, start: int, end: int) -> str:
    parts = []
    for number in range(start, end + 1):
        text = store.plan_chapter_text(number)
        if text:
            parts.append(text)
    return "\n\n".join(parts) or "(в плане нет этих глав)"


_ARC_STEP = re.compile(r"^(?:#+\s*)?Глава\s+\d+\b", re.IGNORECASE)


def fill_arc_from_plan(chapters: list[dict], arc: dict, start: int, end: int) -> dict:
    """Пустое обещание не берётся из снятой колонки «ещё рано». Выплата остаётся внутри окна."""
    if not int(arc.get("payoff_chapter") or 0):
        from app.pipeline.scale import CHAPTER_CAP

        arc["payoff_chapter"] = min(CHAPTER_CAP, end)
    return arc


_FREEZE = re.compile(
    r"не открыва|не трога|не вскрыва|не раскрыва|игнорир|не обраща|без изучения|нетронут|не изуч",
    re.IGNORECASE,
)
_SOFT_FREEZE = re.compile(
    r"продолжает|работает дальше|тем же делом|то же занятие",
    re.IGNORECASE,
)
_STRONG_TURN = re.compile(
    r"приход|ошиб|пряч|замеча|теря|спор|стук|пута|меша|отмен|встреч|потер",
    re.IGNORECASE,
)
_STEP_SPLIT = re.compile(r"^(?:#+\s*)?Глава\s+(\d+)\b[:\s—-]*(.*)$", re.IGNORECASE)
def _sheet_turn(drama: dict | None, index: int, promise: str) -> str:
    """Плоский шаг берёт цену и убеждение этой книги, а не чужой сюжет."""
    secret = (promise or "секрет").strip()
    if len(secret) > 80:
        secret = secret[:80].rstrip()
    data = drama or {}
    cost = (data.get("cost") or "").strip()
    belief = (data.get("belief") or "").strip()
    if cost or belief:
        variants = (
            f"Герой платит: {cost or 'своим'}. Убеждение пока такое: {belief or 'прежнее'}. Секрет закрыт: {secret}.",
            f"Плата «{cost or 'своя'}» уже видна. Убеждение «{belief or 'прежнее'}» не даёт назвать секрет: {secret}.",
            f"Из-за убеждения «{belief or 'прежнее'}» герой выбирает плату «{cost or 'свою'}» и меняет положение. Секрет закрыт: {secret}.",
        )
        return f"{variants[index % 3]} Шаг окна {index + 1}."
    return f"Положение меняется шагом {index + 1}, секрет остаётся закрытым: {secret}."


def step_is_flat(body: str) -> bool:
    """Шаг, который только повторяет запрет, развитием не считается."""
    text = (body or "").strip()
    if len(text.split()) < 4:
        return True
    if _STRONG_TURN.search(text):
        return False
    return _FREEZE.search(text) is not None or _SOFT_FREEZE.search(text) is not None


def _split_steps(steps: str) -> dict[int, str]:
    found: dict[int, str] = {}
    number: int | None = None
    buffer: list[str] = []

    def flush() -> None:
        if number is not None:
            found[number] = " ".join(part for part in buffer if part).strip()

    for raw in (steps or "").splitlines():
        line = raw.strip()
        match = _STEP_SPLIT.match(line)
        if match:
            flush()
            number = int(match.group(1))
            buffer = [match.group(2).strip()]
        elif number is not None and line:
            buffer.append(line)
    flush()
    return found


def sharpen_arc(arc: dict, chapters: list[dict], drama: dict | None = None) -> dict:
    """Плоский повтор запрета заменяется поворотом. Живой шаг модели сохраняется."""
    sharpened = dict(arc)
    start = int(sharpened.get("start_chapter") or 1)
    end = int(sharpened.get("end_chapter") or start)
    by_number = {int(row["number"]): row for row in chapters}
    parsed = _split_steps(sharpened.get("steps") or "")
    lines: list[str] = []
    for index, number in enumerate(range(start, end + 1)):
        body = parsed.get(number, "")
        event = ((by_number.get(number) or {}).get("event") or "").strip()
        if body and not step_is_flat(body):
            chosen = body
        elif event and not step_is_flat(event):
            chosen = event
        else:
            chosen = _sheet_turn(drama, index, sharpened.get("promise") or "")
        lines.append(f"Глава {number}: {chosen}")
    sharpened["steps"] = "\n".join(lines)
    return sharpened


def _stems(text: str) -> set[str]:
    return {word.lower()[:5] for word in re.findall(r"[А-Яа-яЁё]{5,}", text or "")}


def arc_fits_plan(arc: dict | None, chapters: list[dict], number: int) -> bool:
    """Окно от старого плана не считается годным: у шага этой главы нет общих слов с событием."""
    if not arc or not (arc.get("steps") or "").strip():
        return False
    promise = (arc.get("promise") or "").lower()
    if "ещё рано" in promise or "еще рано" in promise or "рано говорить" in promise:
        return False
    event = ""
    for row in chapters:
        if int(row["number"]) == number:
            event = (row.get("event") or "").strip()
            break
    if not event:
        return False
    step = ""
    for line in (arc.get("steps") or "").splitlines():
        if re.match(rf"глава\s+{number}\b", line.strip(), flags=re.I):
            step = line
            break
    if not step:
        return False
    return bool(_stems(step) & _stems(event))


def rebuild_arc_from_plan(book: BookProject, number: int) -> str:
    """Окно на пять глав из текущего плана, без вызова модели и без старого сюжета."""
    from app.pipeline.plan import _store
    from app.pipeline.scale import CHAPTER_CAP

    store = _store(book)
    chapters = store.plan_chapters()
    by_number = {int(row["number"]): row for row in chapters}
    if not by_number:
        return store.arc_text()
    end = min(CHAPTER_CAP, max(by_number), number + 4)
    hidden = ((store.world().get("hidden") or "").replace("*", " ")).strip()
    promise = hidden.split(".")[0].strip()[:180] if hidden else "Тайна из листа мира остаётся закрытой."
    lines: list[str] = []
    for current in range(number, end + 1):
        event = ((by_number.get(current) or {}).get("event") or "").strip()
        brief = " ".join(event.split()[:40])
        lines.append(f"Глава {current}: {brief}" if brief else f"Глава {current}: положение меняется")
    arc = sharpen_arc(
        {
            "promise": promise,
            "payoff_chapter": end,
            "start_chapter": number,
            "end_chapter": end,
            "steps": "\n".join(lines),
        },
        chapters,
        store.drama(),
    )
    store.save_arc(arc)
    return store.arc_text()


def ensure_fresh_arc(book: BookProject, *, force: bool = False) -> str:
    from app.pipeline.plan import _store

    store = _store(book)
    number = int(book.read_state().get("chapter_number") or 1)
    chapters = store.plan_chapters()
    if not force and arc_fits_plan(store.arc(), chapters, number):
        return store.arc_text()
    try:
        return rebuild_arc_from_plan(book, number)
    except Exception:
        store.clear_arc()
        return ""


def persist_sharp_arc(book: BookProject) -> None:
    """Уже записанное плоское окно чинится до следующей сцены, без нового вызова модели."""
    from app.pipeline.plan import _store

    store = _store(book)
    arc = store.arc()
    if not arc or not ((arc.get("promise") or "").strip() or (arc.get("steps") or "").strip()):
        return
    sharpened = sharpen_arc(arc, store.plan_chapters(), store.drama())
    if (sharpened.get("steps") or "") != (arc.get("steps") or ""):
        store.save_arc(sharpened)


def _parse_arc(text: str, start: int, end: int) -> dict | None:
    promise = ""
    payoff = 0
    steps: list[str] = []
    current = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        lower = line.lower()
        if lower.startswith("обещание"):
            parts = re.split(r"[:—-]", line, maxsplit=1)
            promise = parts[1].strip() if len(parts) > 1 else promise
        elif "выплата" in lower:
            numbers = re.findall(r"\d+", line)
            if numbers:
                payoff = int(numbers[0])
        elif _ARC_STEP.match(line) and "|" not in line:
            if current:
                steps.append(current.strip())
            current = line.lstrip("#").strip()
        elif current and line:
            current += "\n" + line
    if current:
        steps.append(current.strip())
    if not promise and not steps:
        return None
    return {
        "promise": promise,
        "payoff_chapter": payoff,
        "start_chapter": start,
        "end_chapter": end,
        "steps": "\n".join(steps),
    }
