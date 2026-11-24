from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.llm.router import complete_for, complete_with_continue
from app.pipeline.anti_llm import de_llm
from app.pipeline.prompts import (
    BIBLE_NAMES,
    CANON_RULE,
    HUMAN_PROSE,
    KEEP_LENGTH,
    pack_priority,
    word_count,
)
from app.storage.project import BookProject

Progress = Callable[[str, dict[str, Any]], None]
MAX_SCENES = 6
STITCH_KEEP_RATIO = 0.90
_SCENE_HEAD = re.compile(r"(?m)^###\s+Сцена\s+\d+.*$")

_CONTRACT_EXAMPLE = """
Пример формата, не сюжет книги. Имена из примера в книгу не переноси.
# Контракт главы 1
Место и время: ночь, одна комната
Участники: Марек
Исходное состояние: Марек сидит за столом
Изменение: он слышит шаг за дверью и остаётся сидеть
Нельзя раскрывать: кто стоит за дверью
Концовка: дверь закрыта, Марек в комнате
### Сцена 1
Место и время: ночь, одна комната
Участники: Марек
Исходное состояние: Марек сидит за столом
Изменение: он слышит шаг за дверью и остаётся сидеть
Шаг 1: откладывает работу и слушает
Шаг 2: проверяет засов и не открывает
Шаг 3: садится обратно, шаг за дверью стихает
Нельзя раскрывать: кто стоит за дверью
Концовка: дверь закрыта, Марек в комнате
""".strip()

BEAT_COUNT = 4
_BEAT_JOBS = (
    "Первое событие главы. Читатель видит, где это и кто здесь, и уже в этом куске случается первая неприятность.",
    "Второе событие того же дня. Ответ на первое создаёт новую помеху.",
    "Третье событие того же дня. Главная перемена главы из плана. Следующую главу не начинай.",
    "Четвёртое событие того же дня. Последствие, из-за которого хочется читать дальше. Книгу не заканчивай.",
)

CONTRACT_SYSTEM = f"""Составь контракт одной главы до прозы. Русский. Без художественного текста.
Ровно четыре сцены. Это четыре разных события одного дня, не четыре куска одного жеста и не четыре разных дня.
У главы и у каждой сцены одни и те же поля:
Место и время:
Участники:
Исходное состояние:
Изменение:
Нельзя раскрывать:
Концовка:
Сцена 1: {_BEAT_JOBS[0]}
Сцена 2: {_BEAT_JOBS[1]}
Сцена 3: {_BEAT_JOBS[2]}
Сцена 4: {_BEAT_JOBS[3]}
У каждой сцены своё изменение: отдельное событие, после которого положение другое. К концу главы таких перемен четыре. Если план назвал одну ситуацию, разверни её в четыре события этого дня, не выдумывай другой сюжет.
В каждой сцене три шага. Это три хода одного события, не три новых дня и не готовая проза:
Шаг 1:
Шаг 2:
Шаг 3:
Концовка называет состояние в конце, а не фразу для вставки в главу. Не пиши предложение, которое можно перенести в прозу целиком.
Поворот не может быть пересказом запрета. Если в окне арки есть шаг этой главы, бери изменение оттуда.
«Нельзя раскрывать» — то, что есть в будущих главах. Секрет остаётся закрытым, но глава им не исчерпывается.
Не закрывай выплату арки раньше главы, указанной в окне арки.
Место и время бери из плана и карточки. Не подставляй дорогу, если её там нет.
Часы и дни не пропускай. Следующую главу не начинай.
Если в карточке есть запрет, перепиши его дословно в поле «Нельзя раскрывать» и не заменяй другим смыслом.
{BIBLE_NAMES}
{_CONTRACT_EXAMPLE}
Формат ответа:
# Контракт главы N
Место и время: ...
Участники: ...
Исходное состояние: ...
Изменение: ...
Нельзя раскрывать: ...
Концовка: ...
### Сцена 1
Место и время: ...
Участники: ...
Исходное состояние: ...
Изменение: ...
Нельзя раскрывать: ...
Концовка: ...
### Сцена 2
...
### Сцена 3
...
### Сцена 4
...
"""

_RHYTHM_SAMPLE = """
Образец ритма, не сюжет этой книги. Имя и лампу из образца в текст не переноси.
Лампа коптила. Он поправил фитиль, и на столе стало видно край чужой записки, которую утром так и не прочли.
""".strip()

_BAN_HINT = re.compile(
    r"нельзя|не выходит|не выход|не открыва|не покид|остаётся в|остается в",
    re.IGNORECASE,
)


def _context_parts(book: BookProject, number: int) -> tuple[list[str], list[str]]:
    from app.pipeline.plan import _store

    state = book.read_state()
    store = _store(book)
    prev = book.last_finished_chapters(2)
    endings = [f"# Глава {n}, финал\n{(t or '')[-2500:]}" for n, t in prev]
    plan_slice = store.plan_chapter_text(number)
    from app.pipeline.drama import chapter_drama

    beat = chapter_drama(store.drama(), number)
    arc = store.arc_text()
    from app.pipeline.select import chapter_needles, lines_about

    needles = chapter_needles(plan_slice, state.get("characters") or [])
    spine = store.spine()
    spine_line = ""
    if spine and any(spine.values()):
        from app.pipeline.drama import act_spans

        spans = act_spans(store.drama())
        act = ""
        if spans:
            for index, (start, end) in enumerate(spans):
                if start <= number <= end:
                    act = store.drama().get(f"act{index + 1}") or ""
                    break
        if not act:
            total = int(state.get("chapter_count") or 0)
            if total >= 3:
                third = max(1, total // 3)
                act = spine["act1"] if number <= third else spine["act2"] if number <= third * 2 else spine["act3"]
            else:
                act = spine.get("act1") or ""
        spine_line = "\n".join(
            part
            for part in (
                f"Финал книги: {spine['finale']}" if spine.get("finale") else "",
                f"Этот акт: {act}" if act else "",
            )
            if part
        )
    chars = state.get("characters") or []
    last_seen = state.get("last_seen") or {}
    active = []
    for ch in chars:
        name = ch.get("name")
        if not name:
            continue
        seen = int(last_seen.get(name, 0) or 0)
        if seen >= number - 3 or name.lower() in (plan_slice or "").lower():
            active.append(f"- {name} ({ch.get('status')})")
    if not active:
        active = [f"- {c.get('name')}" for c in chars[:12]]
    opening = (
        "(это глава 1: место и ситуация берутся из карточки и плана, не подставляй дорогу)"
        if number == 1
        else "(предыдущего текста нет)"
    )
    learned = _reader_learns(store, number)
    required = [
        part
        for part in (
            "План этой главы:\n"
            + (plan_slice or f"(нет пункта главы {number} — опирайся на синопсис и предыдущее)"),
            learned,
            "Драма этой главы:\n" + (beat or "(лист драмы ещё не задан)"),
            "Окно арки:\n" + (arc or "(окна арки нет)"),
        "Открытые события:\n" + (store.events_text() or "(пусто)"),
        "Факты:\n" + (lines_about(store.facts_text(), needles) or "(пусто)"),
        "Каркас книги:\n" + (spine_line or "(финал и акты ещё не заданы)"),
        "Повтор ходов:\n" + (store.move_warning(number) or "(одинаковый ход в последних главах не накопился)"),
        "Курс от пользователя (важнее плана):\n"
        + (state.get("course_note") or "(нет — иди по плану)"),
        "Конец предыдущего текста:\n" + ("\n\n".join(endings) or opening),
        (
            "Это оригинал: мир задают карточка и библия."
            if state.get("is_original", True)
            else "Чужая вселенная: канон подтверждают заметки и карточка. Библия без заметки не канон.\n"
            + "Подтверждённые заметки:\n"
            + (store.canon_text() or "(заметок нет — не выдумывай канон)")
        ),
        f"Канон: {'да' if state.get('canon', True) else 'AU'}. 18+: {'да' if state.get('adult') else 'нет'}.",
        CANON_RULE,
        BIBLE_NAMES,
            "Активные персонажи:\n" + ("\n".join(active) or "(см. библию)"),
        )
        if part
    ]
    optional = [
        "Карточка:\n" + book.read_text("card.md")[:3500],
        "Стиль:\n" + book.read_text("style.md")[:3000],
        "Саммари уже произошедшего:\n" + (state.get("summary") or "(начало)")[:2500],
        "Библия (только строки про эту главу):\n"
        + lines_about(book.read_text("bible.md"), needles, 4000),
    ]
    return required, optional


def _reader_learns(store, number: int) -> str:
    """Лист мира и одна строка этой главы. Пустой лист не велит выдумывать справку."""
    labels = (
        ("when_where", "Когда и где"),
        ("daily", "Обычная жизнь"),
        ("first_hour", "Первый час"),
        ("hidden", "Пока скрыто"),
    )
    from app.pipeline.drama import world_act

    act_world = world_act(store.drama(), number)
    world = store.world()
    sheet = [
        f"{label}: {(world.get(key) or '').strip()}"
        for key, label in labels
        if (world.get(key) or "").strip()
    ]
    current = ""
    earlier: list[str] = []
    for row in store.plan_chapters():
        reveal = (row.get("reveal") or "").strip()
        if not reveal:
            continue
        if int(row["number"]) == number:
            current = reveal
        elif int(row["number"]) < number:
            earlier.append(f"Глава {row['number']}: {reveal[:280]}")
    if not sheet and not current and not earlier and not act_world:
        return ""
    parts = []
    if act_world:
        parts.append("Развитие мира в этом акте:\n" + act_world)
    if sheet:
        parts.append("Лист мира:\n" + "\n".join(sheet))
    parts.append(
        "Что читатель узнаёт в этой главе:\n"
        + (current or "(для этой главы не задано — не выдумывай справку о мире)")
    )
    if earlier:
        parts.append("Уже показано читателю:\n" + "\n".join(earlier[-6:]))
    return "\n\n".join(parts)


def _facts_for(book: BookProject) -> str:
    from app.pipeline.plan import _store

    return _store(book).facts_text()


def _prompt(book: BookProject, number: int, extra_required: list[str]) -> str:
    required, optional = _context_parts(book, number)
    return pack_priority(extra_required + required, optional)


def split_scene_contracts(text: str) -> list[str]:
    matches = list(_SCENE_HEAD.finditer(text or ""))
    if not matches:
        body = (text or "").strip()
        return [body] if body else []
    blocks: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.start() : end].strip()
        if block:
            blocks.append(block)
    return blocks[:MAX_SCENES]


def chapter_contract_header(text: str) -> str:
    match = _SCENE_HEAD.search(text or "")
    return (text[: match.start()] if match else text or "").strip()


_SCENE_FILE = re.compile(r"scene-(\d+)\.md$")


def scene_part_path(book: BookProject, number: int, index: int):
    return book.chapters_dir / f"{number:03d}-scene-{index:02d}.md"


def write_scene_text(book: BookProject, number: int, index: int, text: str) -> None:
    book.chapters_dir.mkdir(parents=True, exist_ok=True)
    path = scene_part_path(book, number, index)
    body = (text or "").replace("\r\n", "\n")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def read_scene_texts(book: BookProject, number: int) -> list[str]:
    found: dict[int, str] = {}
    if not book.chapters_dir.exists():
        return []
    for path in book.chapters_dir.glob(f"{number:03d}-scene-*.md"):
        match = _SCENE_FILE.search(path.name)
        if not match:
            continue
        found[int(match.group(1))] = path.read_text(encoding="utf-8").strip()
    texts: list[str] = []
    index = 1
    while found.get(index):
        texts.append(found[index])
        index += 1
    return texts


def clear_scene_texts(book: BookProject, number: int) -> None:
    if not book.chapters_dir.exists():
        return
    for path in book.chapters_dir.glob(f"{number:03d}-scene-*.md"):
        path.unlink(missing_ok=True)


def scenes_to_reuse(book: BookProject, number: int) -> tuple[str, list[str]]:
    """Оборванный запуск продолжается. Черновик новее кусков запускает главу с начала."""
    scenes = read_scene_texts(book, number)
    contract = book.read_contract(number).strip() if scenes else ""
    if scenes and contract and _scenes_are_newer_than_draft(book, number):
        return contract, scenes
    if book.read_chapter(number, draft=True).strip():
        return "", []
    if not scenes or not contract:
        return "", []
    return contract, scenes


def _scenes_are_newer_than_draft(book: BookProject, number: int) -> bool:
    draft = book.chapter_path(number, draft=True)
    if not draft.exists():
        return True
    scenes = list(book.chapters_dir.glob(f"{number:03d}-scene-*.md"))
    if not scenes:
        return False
    return max(path.stat().st_mtime for path in scenes) > draft.stat().st_mtime


def _context(book: BookProject, number: int) -> str:
    return _prompt(book, number, [])


_OPENING = """
Это первая глава, и она длиннее следующих. Пиши событие целиком, той же плотностью подробностей.
Введи читателя внутрь дела, без справки и без лекции:
кто этот человек;
где он и какое сейчас время;
как устроен его обычный день;
кто рядом и как здесь принято себя вести.
Бери это из карточки, плана, листа мира и абзаца «Что читатель узнаёт». Чего там нет, не выдумывай.
Дорогу не добавляй, если её нет в замысле. Новый день и следующую главу не добавляй.
"""


def _scene_system(number: int, scene_i: int) -> str:
    opening = ""
    if number == 1 and scene_i == 1:
        opening = (
            _OPENING
            + """
Это первое событие. Не начинай с «ты кто?» и не начинай с боя, которого нет в плане.
Сначала человек, место, время и обычный день, и в этом же куске первая неприятность уже происходит.
Место и занятие возьми из карточки и плана: комната, работа, ночь, разговор или последствие прошлого дела.
Если в задании есть абзац «Что читатель узнаёт в этой главе», покажи его здесь целиком, предметами и привычками.
Если там сказано, что в заметках этого нет, не выдумывай.
"""
        )
    elif number == 1:
        opening = (
            _OPENING
            + """
Это новое событие того же дня. Знакомство не начинай справкой заново: люди и место остаются понятны, пока дело идёт дальше.
"""
        )
    elif scene_i == 1:
        opening = """
Если в задании есть абзац «Что читатель узнаёт в этой главе» и он не говорит, что в заметках этого нет, покажи названные там место, время и видимые вещи. Новую сторону мира бери из этого абзаца и из «Развития мира в этом акте».
Знакомство первой главы не повторяй и лекцию не читай. Если абзаца нет, веди главу как раньше, без справки о мире.
"""
    return f"""Пишешь одну сцену главы {number}. Только русский.
{HUMAN_PROSE}
{BIBLE_NAMES}
{_RHYTHM_SAMPLE}
{opening}
Это кусок {scene_i} из {BEAT_COUNT} одной главы: {_BEAT_JOBS[scene_i - 1]}.
{"Продолжай ту же главу с места, где кончился прошлый кусок. Погоду, место и уже отданный приказ заново не открывай." if scene_i > 1 else ""}
Доведи событие этого куска до конца: изменение из задания происходит на странице, с поступками, репликами и последствиями. Не останавливайся на подходе к событию. Это не конспект.
Три шага из задания происходят по порядку. Концовку контракта не переноси в текст готовой фразой.
Часы и дни не пропускай. Следующую главу не начинай. Новых людей сверх задания не добавляй.
Секрет из запрета и из окна арки не раскрывай.
Если окно арки даёт шаг этой главы, событие бери из этого шага и оставь его куску, которому оно задано.
Внутренний ход бери из блока «Драма этой главы»: чего герой хочет, во что верит и чего это стоит.
Не произноси эту веру вслух и не заканчивай моралью. Покажи её поступком.
Не начинай с названия главы.
Запрет карточки выполни дословно."""


def card_ban_lines(card: str) -> list[str]:
    """Предложения карточки, которые проза не имеет права пересказать своими словами."""
    parts = re.split(r"\n+|(?<=[.!?])\s+", card or "")
    lines: list[str] = []
    seen: set[str] = set()
    for part in parts:
        sentence = part.strip()
        if not sentence or not _BAN_HINT.search(sentence):
            continue
        key = sentence.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(sentence)
    return lines


def with_card_bans(contract: str, card: str) -> str:
    lines = card_ban_lines(card)
    if not lines or "Запрет карточки:" in (contract or ""):
        return contract
    block = "Запрет карточки:\n" + "\n".join(f"- {line}" for line in lines)
    return (contract.rstrip() + "\n\n" + block).strip()


def _hold_to_plan(book: BookProject, number: int, text: str) -> tuple[str, list[str]]:
    """Сообщает о скачке и оставляет текст целиком. Обрезок в черновик не пишется."""
    from app.pipeline.bounds import plan_bound_issues
    from app.pipeline.plan import _store

    plan = _store(book).plan_chapter_text(number)
    return text, plan_bound_issues(text, plan)


def _beat_blocks(contract: str, number: int = 0) -> list[str]:
    found = split_scene_contracts(contract)
    header = chapter_contract_header(contract)
    blocks: list[str] = []
    for index, job in enumerate(_BEAT_JOBS, start=1):
        source = found[index - 1] if index <= len(found) else ""
        body = source.strip() or f"### Сцена {index}\n{header}"
        if number == 1 and index == 1:
            job += (
                " Здесь же читатель понимает, кто это, где это, какое время и чем занят обычный день."
            )
        blocks.append(f"{body}\nЗадача этого куска: {job}")
    return blocks


def _write_beat(
    book: BookProject,
    number: int,
    index: int,
    block: str,
    earlier: list[str],
    prior_blocks: list[str] | None = None,
) -> str:
    header = chapter_contract_header(book.read_contract(number))
    ban_lines = card_ban_lines(book.read_text("card.md"))
    ban_block = "\n".join(f"- {line}" for line in ban_lines) or "(в карточке нет отдельного запрета)"
    piece = complete_with_continue(
        "chapter",
        [
            {"role": "system", "content": _scene_system(number, index)},
            {
                "role": "user",
                "content": _prompt(
                    book,
                    number,
                    [
                        "Контракт главы:\n" + header,
                        "Пиши только этот кусок:\n" + block,
                        "Запрет карточки, дословно:\n" + ban_block,
                        "Секрет не раскрывай. "
                        "Внутренний ход возьми из блока «Драма этой главы» и покажи его поступком, не репликой о смысле.",
                        _prior_note(earlier, prior_blocks),
                        _opening_order(book) if number == 1 and index == 1 else "",
                    ],
                ),
            },
        ],
        max_tokens=3200 if number == 1 else 2200,
        temperature=0.85,
        max_rounds=2,
    )
    if not piece.ok:
        raise RuntimeError(piece.error or "Кусок главы не сгенерировался")
    text = piece.text.strip()
    write_scene_text(book, number, index, text)
    return text


def _change_line(block: str) -> str:
    match = re.search(r"(?m)^Изменение:\s*(.+)$", block or "")
    return match.group(1).strip() if match else ""


def _prior_note(earlier: list[str], blocks: list[str] | None = None) -> str:
    """Хвост уже написанного и список событий, которые повторять нельзя."""
    changes = []
    for index, block in enumerate(blocks or [], start=1):
        line = _change_line(block)
        if line:
            changes.append(f"{index}. {line}")
    written = [part.strip() for part in earlier if part and part.strip()]
    parts: list[str] = []
    if changes:
        parts.append("Уже случилось, эти строки не повторяй:\n" + "\n".join(changes))
    if written:
        tail = "\n\n".join(written)[-5000:]
        parts.append(
            "Уже написано в этой главе. Продолжай с последнего абзаца, "
            "эти события не повторяй:\n" + tail
        )
    return "\n\n".join(parts)


_PURPOSE_STOP = frozenset(
    "чтобы этого будет если герой герой который которая которые только после перед можно нужно надо".split()
)


def purpose_stems(card: str, want: str = "") -> list[str]:
    """Две-три основы цели из «Хочет» или из оборота «чтобы» в карточке."""
    text = (want or "").strip()
    if not text:
        match = re.search(r"чтобы\s+([^.]{8,160})", card or "", flags=re.I)
        text = match.group(1) if match else ""
    stems: list[str] = []
    for word in re.findall(r"[а-яё]{5,}", text.lower()):
        if word in _PURPOSE_STOP:
            continue
        stem = word[:5]
        if stem not in stems:
            stems.append(stem)
        if len(stems) == 3:
            break
    return stems


def opening_needles(hero: str, when_where: str, card: str, plan: str, want: str = "") -> list[str]:
    """Что первая глава обязана показать, если это уже сказано в замысле."""
    from app.pipeline.select import person_tokens

    blob = "\n".join((when_where or "", card or "", plan or ""))
    found: list[str] = []
    surname = person_tokens(hero)[:1]
    found.extend(surname)
    years = re.findall(r"\b1[0-9]{3}\b", blob)
    if years:
        found.append(years[0])
    low = blob.lower()
    if "росси" in low or "русск" in low:
        found.append("Росс")
    place = re.search(r"\b(?:к|в|до)\s+([А-ЯЁ][а-яё]{4,})", when_where or "")
    if place:
        found.append(place.group(1)[:5])
    found.extend(purpose_stems(card, want))
    unique: list[str] = []
    for item in found:
        if item and item not in unique:
            unique.append(item)
    return unique


def _opening_order(book: BookProject) -> str:
    from app.pipeline.plan import _store

    store = _store(book)
    drama = store.drama() or {}
    needles = opening_needles(
        drama.get("hero") or "",
        (store.world() or {}).get("when_where") or "",
        book.read_text("card.md"),
        store.plan_chapter_text(1),
        drama.get("want") or "",
    )
    if not needles:
        return ""
    return (
        "В этом первом событии читатель обязан понять перечисленное, "
        "если оно есть в листе мира и карточке. Покажи предметами и репликами, не справкой:\n"
        + "\n".join(f"- {item}" for item in needles)
    )


def _missing_opening(text: str, needles: list[str]) -> list[str]:
    low = (text or "").lower()
    return [item for item in needles if item.lower() not in low]


def _weave_opening(book: BookProject, text: str, missing: list[str]) -> str:
    from app.pipeline.anti_llm import split_paragraphs

    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return text
    head = "\n\n".join(paragraphs[:8])
    result = complete_for(
        "revise",
        [
            {
                "role": "system",
                "content": (
                    "Впиши в начало главы то, чего читатель ещё не понял. Русский.\n"
                    "Не добавляй новый день, новое событие и новых людей.\n"
                    "Верни только эти абзацы начала, без остальной главы.\n"
                    f"{KEEP_LENGTH}"
                ),
            },
            {
                "role": "user",
                "content": "Не хватает в тексте:\n"
                + "\n".join(f"- {item}" for item in missing)
                + "\n\nНачало:\n"
                + head,
            },
        ],
        max_tokens=1800,
        temperature=0.4,
    )
    if not result.ok or not result.text.strip():
        return text
    rewritten = result.text.strip()
    if word_count(rewritten) < int(word_count(head) * 0.85):
        return text
    if _missing_opening(rewritten, missing) == missing:
        return text
    tail = "\n\n".join(paragraphs[8:])
    return (rewritten + ("\n\n" + tail if tail else "")).strip()


def _rewrite_seam(left: list[str], right: list[str]) -> list[str] | None:
    from app.pipeline.anti_llm import split_paragraphs

    original = left + right
    body = "\n\n".join(original)
    result = complete_for(
        "revise",
        [
            {
                "role": "system",
                "content": (
                    "Это стык двух событий одного дня. Убери повтор: второе описание той же погоды, "
                    "повтор уже отданного приказа и вторую копию той же развязки.\n"
                    "Остальной текст сохрани. Новый день, новую главу и новых людей не добавляй.\n"
                    f"Верни ровно {len(original)} абзацев через пустую строку.\n"
                    f"{KEEP_LENGTH}"
                ),
            },
            {"role": "user", "content": body},
        ],
        max_tokens=1400,
        temperature=0.3,
    )
    if not result.ok or not result.text.strip():
        return None
    paras = split_paragraphs(result.text)
    if len(paras) != len(original):
        return None
    if word_count(result.text) < STITCH_KEEP_RATIO * word_count(body):
        return None
    return paras


def _join_beats(parts: list[str], number: int = 0) -> str:
    from app.pipeline.anti_llm import split_paragraphs

    groups = [split_paragraphs(part) for part in parts if part and part.strip()]
    if not groups:
        return ""
    if len(groups) == 1:
        return "\n\n".join(groups[0])
    for index in range(len(groups) - 1):
        left = groups[index][-2:]
        right = groups[index + 1][:2]
        if len(left) < 1 or len(right) < 1:
            continue
        fixed = _rewrite_seam(left, right)
        if not fixed:
            continue
        groups[index] = groups[index][: -len(left)] + fixed[: len(left)]
        groups[index + 1] = fixed[len(left) :] + groups[index + 1][len(right) :]
    _ = number
    return "\n\n".join(paragraph for group in groups for paragraph in group)


def generate_chapter(book: BookProject, on_progress: Progress | None = None) -> dict[str, Any]:
    def progress(step: str, **data: Any) -> None:
        if on_progress:
            on_progress(step, data)

    from app.pipeline.continuity import ensure_fresh_arc, persist_sharp_arc
    from app.pipeline.jobs import check_cancel
    from app.pipeline.status_lines import line, prose_line

    ensure_fresh_arc(book)
    persist_sharp_arc(book)
    state = book.read_state()
    number = int(state.get("chapter_number") or 1)
    reused_contract, saved_scenes = scenes_to_reuse(book, number)
    opening_rule = ""
    if number == 1:
        opening_rule = """
Это первая глава. Она длиннее обычной и завлекает.
Первое событие знакомит: кто герой, где он, какое время, чем занят обычный день и кто рядом. В том же событии уже случается первая неприятность.
Дальше три других события этого дня двигают сюжет. Главу не трать на осмотр без происшествия.
Новый день и следующую главу не добавляй. Не подставляй дорогу, если её нет в замысле.
"""
    provider_note = None
    pieces: list[str] = []
    if reused_contract and saved_scenes:
        progress("contract", message="Уже записанные куски оставляю.")
        contract = with_card_bans(reused_contract, book.read_text("card.md"))
        book.write_contract(number, contract)
        pieces = list(saved_scenes)
    else:
        if book.read_chapter(number, draft=True).strip():
            clear_scene_texts(book, number)
        progress("contract", message=line("contract"))
        contract_result = complete_with_continue(
            "chapter",
            [
                {"role": "system", "content": CONTRACT_SYSTEM + opening_rule},
                {"role": "user", "content": _prompt(book, number, [f"Номер главы: {number}"])},
            ],
            max_tokens=1800,
            temperature=0.4,
            max_rounds=2,
        )
        if not contract_result.ok:
            raise RuntimeError(contract_result.error or "Не удалось составить контракт главы")
        contract = with_card_bans(contract_result.text.strip(), book.read_text("card.md"))
        book.write_contract(number, contract)
    blocks = _beat_blocks(contract, number)
    for index in range(len(pieces) + 1, BEAT_COUNT + 1):
        check_cancel()
        progress(
            "prose",
            message=prose_line(index - 1, BEAT_COUNT),
            scene=index,
            done=index - 1,
            total=BEAT_COUNT,
        )
        pieces.append(_write_beat(book, number, index, blocks[index - 1], pieces, blocks[: index - 1]))
        progress(
            "prose",
            message=prose_line(index, BEAT_COUNT),
            scene=index,
            done=index,
            total=BEAT_COUNT,
        )
    progress("stitch", message=line("stitch"))
    joined = _join_beats(pieces[:BEAT_COUNT], number)
    progress("anti_llm", message=line("anti_llm"))
    from app.pipeline.plan import _store

    world_sheet = _store(book).world()
    world_text = "\n".join(str(value) for value in world_sheet.values() if value)
    cleaned = de_llm(
        joined,
        book.read_text("style.md"),
        contract,
        _facts_for(book),
        world_text,
        always=True,
    )
    if number == 1:
        missing = _missing_opening(
            cleaned,
            opening_needles(
                (_store(book).drama() or {}).get("hero") or "",
                world_sheet.get("when_where") or "",
                book.read_text("card.md"),
                _store(book).plan_chapter_text(1),
                (_store(book).drama() or {}).get("want") or "",
            ),
        )
        if missing:
            cleaned = _weave_opening(book, cleaned, missing)

    progress("bounds", message=line("bounds"))
    cleaned, bounds = _hold_to_plan(book, number, cleaned)
    from app.pipeline.bounds import contract_issues

    dead = [
        item.get("name")
        for item in (book.read_state().get("characters") or [])
        if item.get("status") == "dead" and item.get("name")
    ]
    bounds = bounds + contract_issues(cleaned, contract, dead)
    book.write_chapter(number, cleaned, draft=True)

    progress("overlap", message=line("overlap"))
    from app.pipeline.overlap import check_draft_overlap

    try:
        overlaps = check_draft_overlap(book, cleaned, number)
    except Exception as exc:
        overlaps = [{"error": f"Эмбеддинги недоступны: {exc}"}]
    return {
        "chapter": number,
        "text": cleaned,
        "word_count": word_count(cleaned),
        "overlaps": overlaps,
        "bounds": bounds,
        "provider_note": provider_note,
    }


def revise_fragment(book: BookProject, selection: str, comment: str) -> str:
    state = book.read_state()
    number = int(state.get("chapter_number") or 1)
    draft = book.read_chapter(number, draft=True)
    if not draft.strip():
        raise RuntimeError("Нет черновика")
    if not selection.strip():
        raise RuntimeError("Выделите фрагмент для доработки")
    result = complete_for(
        "revise",
        [
            {
                "role": "system",
                "content": f"""Замени ТОЛЬКО указанный фрагмент главы по замечанию. Верни новый фрагмент, не всю главу.
{HUMAN_PROSE}
{KEEP_LENGTH}
{BIBLE_NAMES}
Сохрани имена и факты вокруг.""",
            },
            {
                "role": "user",
                "content": pack_priority(
                    [
                        "Замечание:\n" + (comment or "Перепиши живее, без нейросетевого налёта."),
                        "Фрагмент:\n" + selection,
                        "Конец черновика:\n" + draft[-1500:],
                        "Контракт главы:\n" + (book.read_contract(number) or "(нет)"),
                    ],
                    [
                        "Начало черновика:\n" + draft[:2000],
                        "Стиль:\n" + book.read_text("style.md")[:2000],
                    ],
                ),
            },
        ],
        max_tokens=2000,
        temperature=0.8,
    )
    if not result.ok:
        raise RuntimeError(result.error or "Не удалось переписать фрагмент")
    if selection not in draft:
        new_draft = draft + "\n\n" + result.text
    else:
        new_draft = draft.replace(selection, result.text.strip(), 1)
    book.write_chapter(number, new_draft, draft=True)
    return new_draft


def accept_chapter(book: BookProject) -> dict[str, Any]:
    from app.pipeline.continuity import (
        append_timeline,
        missing_character_hints,
        refresh_facts,
        update_last_seen_from_text,
        update_summary,
        refresh_arc,
        refresh_events,
    )
    from app.pipeline.overlap import remember_chapter

    from app.pipeline.bounds import acceptance_block
    from app.pipeline.plan import _store

    state = book.read_state()
    number = int(state.get("chapter_number") or 1)
    draft = book.read_chapter(number, draft=True)
    if not draft.strip():
        raise RuntimeError("Нет черновика для принятия")
    store = _store(book)
    dead = [
        item.get("name")
        for item in (state.get("characters") or [])
        if item.get("status") == "dead" and item.get("name")
    ]
    blocked = acceptance_block(
        draft,
        store.plan_chapter_text(number),
        book.read_contract(number),
        dead,
    )
    if blocked:
        raise RuntimeError(blocked)
    update_last_seen_from_text(book, number, draft)
    refresh_facts(book, number, draft)
    append_timeline(book, number, draft)
    update_summary(book, number, draft)
    refresh_events(book, number, draft)
    refresh_arc(book, number + 1)
    chapter_row = next((row for row in store.plan_chapters() if int(row["number"]) == number), None)
    store.record_moves(number, (chapter_row or {}).get("tags") or "")
    remember_chapter(book, number, draft)
    book.write_chapter(number, draft, draft=False)
    book.patch_state(chapter_number=number + 1, course_note="", stage="chapter")
    hints = missing_character_hints(book)
    return {"accepted": number, "next": number + 1, "hints": hints}
