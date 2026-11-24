from __future__ import annotations

import re

from app.llm.router import complete_with_continue
from app.pipeline.prompts import CANON_RULE, comment_block, pack_priority
from app.pipeline.scale import CHAPTER_CAP, book_band
from app.storage.project import BookProject


def plan_system(low: int, high: int) -> str:
    return f"""Ты составляешь план романа на русском.
{CANON_RULE}
План должен следовать синопсису из карточки.

Сначала выбери число глав N от {low} до {high} включительно.
N — сколько глав нужно этой истории, чтобы перемена героя успела случиться. Не заполняй диапазон до края, если сюжет короче.
Первая строка ответа ровно такая: Глав: N

На одну главу — три или четыре отдельных события одного дня. Не один жест и не весь акт.
«Кратко» описывает только то, что успевает случиться В ЭТОЙ главе.
В «Кратко» явно назови место и время этой главы: по ним потом пишется, что видит читатель. Стороны мира, обычай и правило жизни в «Кратко» не пересказывай.
Раскрытие личности, титула, тайны крови, «клятва на всю жизнь» — не в главе 1, если синопсис можно растянуть.
Глава 1: первое событие знакомит с человеком, местом, временем и обычным днём и уже содержит неприятность. Дальше в этой же главе ещё события, которые завлекают. Без полного бэкстори и без финала знакомства. Дорогу не подставляй, если её нет в замысле.

Перед главами напиши четыре поля. У каждого несколько предложений, следующие строки без новой подписи относятся к тому же полю:
Финал: чем кончается книга и какую цену герой за это платит
Акт 1: главы A–B, что давит на героя, чем положение в конце акта отличается от начала
Акт 2: главы C–D, что давит на героя, чем положение в конце акта отличается от начала
Акт 3: главы E–F, что давит на героя, чем положение в конце акта отличается от начала
Три отрезка вместе закрывают 1–N, без дыр и без наложений. Длину акта выбери по этой истории.

Для каждой главы:
## Глава N. Название
Теги: 2–4 метки сцен через запятую (например: засада, торг, ночь, разговор)
Кратко: несколько предложений, не одна фраза и не весь акт.
Где и когда, кто в этой главе.
С чего положение начинается.
Какие три или четыре события успевают случиться, по порядку. У каждого своё изменение.
Чем глава кончается и чем это отличается от начала. Последнее событие оставляет причину читать дальше.
Чего в этой главе ещё нет: следующая глава и финал книги сюда не входят.
Поворот — то, чем конец главы отличается от начала: пришёл человек, ошибка, цена, решение, помеха.
Поворот не может быть пересказом запрета («не открывает», «продолжает то же дело», «игнорирует тайну»).
Соседние главы не повторяют один и тот же поворот. Тайна может оставаться закрытой несколько глав, но положение героя каждый раз меняется.
Лист драмы уже задан отдельно: чего герой хочет, во что верит, чем платит и кем становится.
События акта делают перемену этого акта неизбежной.
Не подменяй событие пересказом веры. Вся перемена не помещается в главу 1.
Акт 1: вера ещё держится. Акт 2: вера стоит цены. Акт 3: выбор, который старая вера не объясняет.
Поля «Финал» и «Акт» — про события. Перемену героя другими словами не переписывай: она уже в листе драмы.
Без предисловий до главы 1.
Не заменяй пропущенные главы многоточием. Нужны все номера от 1 до N и ни одной главы после N.
"""


SYSTEM = plan_system(25, 60)

_HEADING = re.compile(r"(?m)^##\s*Глава\s+(\d+)\b[^\n]*")
_ELLIPSIS = re.compile(r"(?m)^\s*(?:\.{3,}|…+)\s*$")
_FILL_ROUNDS = 3


def generate_plan(book: BookProject, comment: str | None = None) -> str:
    from app.pipeline.drama import ensure_drama

    ensure_drama(book)
    store = _store(book)
    expected = _drama_chapter_count(book)
    text = _request_plan(book, comment, expected)
    spine = parse_spine(text)
    if spine:
        store.save_spine(spine)
    declared = plan_target(text)
    goal = expected or declared
    if goal:
        book.patch_state(chapter_count=goal)
    store.replace_plan(rows_from_plan_text(text))
    text = store.render_plan() or text
    for _ in range(_FILL_ROUNDS):
        targets = missing_chapter_numbers(text, goal or None) or weak_chapter_numbers(text)
        if not targets and not _ELLIPSIS.search(text or ""):
            break
        if not targets:
            break
        try:
            extra = _request_missing(book, text, targets, comment)
        except RuntimeError:
            break
        store.upsert_plan(rows_from_plan_text(extra))
        text = store.render_plan()
    from app.pipeline.continuity import ensure_fresh_arc
    from app.pipeline.world import fill_world

    ensure_fresh_arc(book, force=True)
    try:
        fill_world(book, comment)
    except RuntimeError as exc:
        raise RuntimeError(
            "План глав записан. Лист мира не собрался: "
            f"{exc}. Откройте книгу ещё раз и перепишите план, если лист нужен."
        ) from exc
    return text


def _drama_chapter_count(book: BookProject) -> int | None:
    """Конец третьего акта, если лист уже закрыл 1–N внутри выбранного размера."""
    from app.pipeline.drama import act_spans

    spans = act_spans(_store(book).drama())
    if not spans or spans[0][0] != 1:
        return None
    previous = 0
    for start, end in spans:
        if start != previous + 1:
            return None
        previous = end
    _label, low, high = book_band(book)
    if low <= previous <= high:
        return previous
    return None


def chapter_start_block(book: BookProject) -> str:
    return book_plan_block_reason(book)


def book_plan_block_reason(book: BookProject) -> str:
    text = _store(book).render_plan()
    reason = plan_block_reason(text)
    if reason:
        return reason
    count = plan_target(text)
    label, low, high = book_band(book)
    if not low <= count <= high:
        return (
            f"Для размера «{label}» нужно от {low} до {high} глав. В плане {count}."
        )
    return ""


def parse_spine(text: str) -> dict[str, str]:
    chunks = {"finale": [], "act1": [], "act2": [], "act3": []}
    current = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or _ELLIPSIS.match(line):
            current = ""
            continue
        lowered = line.lower()
        if lowered.startswith("глав:") or lowered.startswith("теги:") or lowered.startswith("кратко:"):
            current = ""
            continue
        if lowered.startswith("финал:"):
            current = "finale"
            chunks[current].append(line.split(":", 1)[1].strip())
            continue
        match = re.match(r"Акт\s+([123])\s*:\s*(.*)", line, flags=re.IGNORECASE)
        if match:
            current = f"act{match.group(1)}"
            if match.group(2).strip():
                chunks[current].append(match.group(2).strip())
            continue
        if current:
            chunks[current].append(line)
    finale = "\n".join(part for part in chunks["finale"] if part).strip()
    acts = {key: "\n".join(part for part in chunks[key] if part).strip() for key in ("act1", "act2", "act3")}
    if not finale and not any(acts.values()):
        return {}
    return {"finale": finale, **acts}


def rows_from_plan_text(text: str) -> list[dict[str, str | int]]:
    rows = []
    for number, block in parse_chapters(text).items():
        rows.append(_row_from_block(number, block))
    return rows


def _store(book: BookProject):
    from app.storage.story import StoryStore

    store = StoryStore(book.root)
    store.import_legacy(book.read_text("plan.md"), book.read_text("facts.md"))
    return store


def plan_target(text: str) -> int:
    """Сколько глав обещает этот текст. Строка «Глав: N» важнее последней написанной главы."""
    chapters = parse_chapters(text)
    highest = max(chapters) if chapters else 0
    declared = re.search(r"(?mi)^Глав:\s*(\d+)\b", text or "")
    if declared:
        number = int(declared.group(1))
        if 1 <= number <= CHAPTER_CAP:
            return number
    return highest


def plan_issues(text: str) -> list[str]:
    if not (text or "").strip():
        return ["План пуст."]
    issues: list[str] = []
    if _ELLIPSIS.search(text):
        issues.append("В плане многоточие вместо блока глав.")
    chapters = parse_chapters(text)
    target = plan_target(text)
    if target <= 0:
        issues.append("В плане нет глав.")
        return issues
    missing = [n for n in range(1, target + 1) if n not in chapters]
    if missing:
        issues.append(f"Нет глав: {_ranges(missing)}.")
    extra = [n for n in chapters if n > target]
    if extra:
        issues.append(f"Есть главы после {target}: {_ranges(extra)}.")
    for number in range(1, target + 1):
        body = chapters.get(number, "")
        if body and _chapter_is_weak(body):
            issues.append(f"Глава {number} без события и отложенного последствия.")
    return issues


def plan_block_reason(text: str) -> str:
    issues = plan_issues(text)
    if not issues:
        return ""
    shown = issues[:8]
    extra = len(issues) - len(shown)
    message = "По этому плану ещё нельзя писать главу. " + " ".join(shown)
    if extra:
        message += f" И ещё {extra}."
    return message


def parse_chapters(text: str) -> dict[int, str]:
    matches = list(_HEADING.finditer(text or ""))
    chapters: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        number = int(match.group(1))
        if 1 <= number <= CHAPTER_CAP:
            chapters[number] = _clean_block(text[match.start() : end])
    return chapters


def missing_chapter_numbers(text: str, target: int | None = None) -> list[int]:
    chapters = parse_chapters(text)
    goal = target or plan_target(text)
    return [n for n in range(1, goal + 1) if n not in chapters]


def weak_chapter_numbers(text: str) -> list[int]:
    chapters = parse_chapters(text)
    return [n for n, body in chapters.items() if _chapter_is_weak(body)]


def _drama_block(book: BookProject) -> str:
    from app.pipeline.drama import drama_ready, render_drama

    data = _store(book).drama()
    if not drama_ready(data):
        return "Лист драмы:\n(ещё не задан)"
    return "Лист драмы (события ему служат, линию не заменяй):\n" + render_drama(data)


def _request_plan(book: BookProject, comment: str | None, expected: int | None) -> str:
    label, low, high = book_band(book)
    if expected:
        order = (
            f"Размер «{label}». Лист драмы уже выбрал {expected} глав. "
            f"Напиши ровно главы 1–{expected}. Первая строка: Глав: {expected}."
        )
        empty = f"(пусто — все {expected} глав)"
    else:
        order = (
            f"Размер «{label}»: выбери N от {low} до {high} и напиши главы 1–N. "
            "Первая строка: Глав: N."
        )
        empty = f"(пусто — главы в диапазоне {low}–{high})"
    result = complete_with_continue(
        "plan",
        [
            {"role": "system", "content": plan_system(low, high)},
            {
                "role": "user",
                "content": pack_priority(
                    [
                        comment_block(comment),
                        order,
                        "Если уже есть обрезок — дополни недостающие номера, не ломая утверждённую логику.",
                        _drama_block(book),
                    ],
                    [
                        "Карточка:\n" + book.read_text("card.md"),
                        "Библия:\n" + book.read_text("bible.md")[:8000],
                        "Стиль:\n" + book.read_text("style.md")[:2000],
                        "Текущий план:\n" + (_store(book).render_plan() or empty),
                    ],
                ),
            },
        ],
        max_tokens=4000,
        temperature=0.65,
        max_rounds=5,
    )
    if not result.ok:
        raise RuntimeError(result.error or "Не удалось составить план")
    return result.text


def _request_missing(
    book: BookProject,
    current: str,
    numbers: list[int],
    comment: str | None,
) -> str:
    ready = parse_chapters(current)
    brief = "\n".join(
        ready[n].splitlines()[0] for n in sorted(ready) if ready[n].splitlines()
    )
    result = complete_with_continue(
        "plan",
        [
            {"role": "system", "content": plan_system(*book_band(book)[1:])},
            {
                "role": "user",
                "content": pack_priority(
                    [
                        "Допиши только недостающие главы: "
                        + ", ".join(str(n) for n in numbers[:20])
                        + ". Для каждой в «Кратко» напиши несколько предложений: где и когда, кто здесь, "
                        "с чего положение начинается, какие три или четыре события этого дня случаются по порядку, "
                        "чем глава кончается и чего в ней ещё нет. "
                        "Событие двигает лист драмы этого акта. Не ставь многоточие вместо глав и не повторяй уже готовые номера.",
                        comment_block(comment),
                        _drama_block(book),
                    ],
                    [
                        "Карточка:\n" + book.read_text("card.md")[:4000],
                        "Уже принятые главы:\n" + brief,
                    ],
                ),
            },
        ],
        max_tokens=4000,
        temperature=0.55,
        max_rounds=3,
    )
    if not result.ok:
        raise RuntimeError(result.error or "Не удалось дополнить план")
    return result.text


def _row_from_block(number: int, block: str) -> dict[str, str | int]:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    title = ""
    heading = re.search(r"Глава\s+\d+\.?\s*(.*)$", lines[0] if lines else "")
    if heading:
        title = heading.group(1).strip(" .")
    tags = ""
    brief_parts: list[str] = []
    for line in lines[1:]:
        if line.lower().startswith("теги:"):
            tags = line.split(":", 1)[1].strip()
        elif line.lower().startswith("кратко:"):
            brief_parts.append(line.split(":", 1)[1].strip())
        else:
            brief_parts.append(line)
    brief = " ".join(part for part in brief_parts if part).strip()
    return {
        "number": number,
        "title": title,
        "tags": tags,
        "event": brief,
        "deferral": "",
    }


def _clean_block(block: str) -> str:
    lines = [line for line in block.splitlines() if not _ELLIPSIS.match(line)]
    return "\n".join(lines).strip()


def _chapter_is_weak(body: str) -> bool:
    words = re.findall(r"\S+", body or "")
    return len(words) < 12


def _ranges(numbers: list[int]) -> str:
    if not numbers:
        return ""
    parts: list[str] = []
    start = prev = numbers[0]
    for number in numbers[1:]:
        if number == prev + 1:
            prev = number
            continue
        parts.append(str(start) if start == prev else f"{start}–{prev}")
        start = prev = number
    parts.append(str(start) if start == prev else f"{start}–{prev}")
    return ", ".join(parts)
