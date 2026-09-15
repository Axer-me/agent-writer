from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.llm.router import complete_for, complete_with_continue
from app.pipeline.anti_llm import de_llm
from app.pipeline.continuity import extract_plan_chapter
from app.pipeline.prompts import BIBLE_NAMES, CANON_RULE, HUMAN_PROSE, KEEP_LENGTH, pack, word_count
from app.storage.project import BookProject

Progress = Callable[[str, dict[str, Any]], None]
MIN_SCENE_WORDS = 700
MAX_SCENES = 6
STITCH_KEEP_RATIO = 0.90


def _context(book: BookProject, number: int) -> str:
    state = book.read_state()
    prev = book.last_finished_chapters(2)
    prev_txt = "\n\n".join(f"# Глава {n}\n{t[-7000:]}" for n, t in prev)
    plan_slice = extract_plan_chapter(book.read_text("plan.md"), number)
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
    return pack(
        "Карточка:",
        book.read_text("card.md")[:3500],
        "Стиль:",
        book.read_text("style.md")[:3000],
        "План этой главы:",
        plan_slice or f"(нет пункта главы {number} — опирайся на синопсис и предыдущее)",
        "Саммари уже произошедшего:",
        (state.get("summary") or "(начало)")[:2500],
        "Факты:",
        book.read_text("facts.md") or "(пусто)",
        "Активные персонажи:",
        "\n".join(active) or "(см. библию)",
        "Библия (фрагмент):",
        book.read_text("bible.md")[:5000],
        "Предыдущие главы:",
        prev_txt or "(это глава 1: нужна затравка, не старт с первого диалога)",
        "Курс от пользователя (если есть — важнее плана):",
        state.get("course_note") or "(нет — иди по плану)",
        f"Канон: {'да' if state.get('canon', True) else 'AU'}. 18+: {'да' if state.get('adult') else 'нет'}.",
        CANON_RULE,
        BIBLE_NAMES,
    )


def _extend_if_short(
    text: str,
    ctx: str,
    scene_block: str,
    *,
    chapter: int,
    scene_i: int,
    min_words: int = MIN_SCENE_WORDS,
    rounds: int = 2,
) -> str:
    current = text
    for _ in range(rounds):
        if word_count(current) >= min_words:
            return current
        extra = complete_for(
            "chapter",
            [
                {
                    "role": "system",
                    "content": f"""Продолжи ту же сцену {scene_i} главы {chapter} с места обрыва.
Не начинай новую веху плана и не переходи к следующей сцене.
{HUMAN_PROSE}
{KEEP_LENGTH}
{BIBLE_NAMES}""",
                },
                {
                    "role": "user",
                    "content": pack(
                        ctx,
                        "Задание сцены (не уходи вперёд):",
                        scene_block,
                        "Уже написано (продолжи, не повторяй):",
                        current[-5000:],
                    ),
                },
            ],
            max_tokens=2200,
            temperature=0.8,
        )
        if not extra.ok or not extra.text.strip():
            break
        current = (current.rstrip() + "\n\n" + extra.text.strip()).strip()
    return current


def _scene_system(number: int, scene_i: int) -> str:
    opening = ""
    if number == 1 and scene_i == 1:
        opening = """
Это ПЕРВАЯ сцена ПЕРВОЙ главы. Не начинай с диалога, боя или «ты кто?».
Сначала затравка: где герой, зачем он уже в пути (если план не задал иное),
погода, тело, дорога — несколько абзацев до встречи или столкновения.
"""
    return f"""Пишешь сцену {scene_i} главы {number} романа. Только русский.
{HUMAN_PROSE}
{KEEP_LENGTH}
{BIBLE_NAMES}
{opening}
Это одна сцена, не конспект всей главы и не следующая сцена.
Минимум около 800 слов. Не начинай с названия главы. Не заканчивай моралью.
Не раскрывай то, что в наброске сцен отнесено к поздним сценам."""


def generate_chapter(book: BookProject, on_progress: Progress | None = None) -> dict[str, Any]:
    def progress(step: str, **data: Any) -> None:
        if on_progress:
            on_progress(step, data)

    state = book.read_state()
    number = int(state.get("chapter_number") or 1)
    ctx = _context(book, number)
    progress("scenes", message="Сцены главы…")

    opening_rule = ""
    if number == 1:
        opening_rule = """
Глава 1: сцена 1 — затравка (герой уже в пути: где, зачем, погода, дорога) до встречи.
Не клади в сцену 1 бой, полное раскрытие личности и клятвы — если это не единственный ход плана.
"""
    scenes = complete_for(
        "chapter",
        [
            {
                "role": "system",
                "content": f"""Разбей главу {number} на 4–6 сцен. Маркер ### Сцена N.
Для каждой сцены: что происходит ТОЛЬКО здесь; отдельной строкой «Нельзя:» — что нельзя раскрывать
(это в плане следующих глав или следующих сцен). Русский. Без прозы.
{opening_rule}
{BIBLE_NAMES}""",
            },
            {"role": "user", "content": ctx},
        ],
        max_tokens=1200,
        temperature=0.6,
    )
    if not scenes.ok:
        raise RuntimeError(scenes.error or "Не удалось набросать сцены")

    parts: list[str] = []
    scene_blocks = [b.strip() for b in scenes.text.split("###") if b.strip()]
    if not scene_blocks:
        scene_blocks = [scenes.text]
    scene_blocks = scene_blocks[:MAX_SCENES]
    for i, block in enumerate(scene_blocks, start=1):
        progress("prose", message=f"Проза сцены {i}/{len(scene_blocks)}…", scene=i)
        piece = complete_with_continue(
            "chapter",
            [
                {"role": "system", "content": _scene_system(number, i)},
                {
                    "role": "user",
                    "content": pack(
                        ctx,
                        "Все сцены главы (пиши только текущую, не забегай):",
                        scenes.text,
                        "Пиши эту сцену:",
                        block,
                        "Уже написанные куски этой главы:",
                        "\n\n".join(parts)[-4000:] or "(это первый кусок)",
                    ),
                },
            ],
            max_tokens=2200,
            temperature=0.85,
            max_rounds=2,
        )
        if not piece.ok:
            raise RuntimeError(piece.error or f"Сцена {i} не сгенерировалась")
        progress("extend", message=f"Дописка сцены {i} при нехватке слов…", scene=i)
        parts.append(_extend_if_short(piece.text, ctx, block, chapter=number, scene_i=i))

    stitched = "\n\n".join(parts)
    progress("logic", message="Склейка швов…")
    logic = complete_for(
        "chapter",
        [
            {
                "role": "system",
                "content": f"""Склей готовые куски в один текст. Подчини швы и противоречия с фактами.
НЕ пересказывай главу заново. НЕ сокращай. НЕ выкидывай быт и дорогу.
Не добавляй мораль от автора. Экспозицию в начале не вырезай.
{HUMAN_PROSE}
{KEEP_LENGTH}
{BIBLE_NAMES}
Верни склеенный текст той же полноты.""",
            },
            {"role": "user", "content": pack(ctx, "Куски (сохрани почти всё):", stitched)},
        ],
        max_tokens=4000,
        temperature=0.4,
    )
    if logic.ok and word_count(logic.text) >= int(word_count(stitched) * STITCH_KEEP_RATIO):
        prose = logic.text
    else:
        prose = stitched

    progress("anti_llm", message="Вычитка «живой автор»…")
    cleaned = de_llm(prose, book.read_text("style.md"))
    book.write_chapter(number, cleaned, draft=True)

    progress("overlap", message="Проверка повторов…")
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
        "provider_note": logic.fallback_from or logic.provider if logic.ok else None,
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
                "content": pack(
                    "Замечание:",
                    comment or "Перепиши живее, без нейросетевого налёта.",
                    "Фрагмент:",
                    selection,
                    "Контекст главы (края):",
                    draft[:2000],
                    "…",
                    draft[-1500:],
                    "Стиль:",
                    book.read_text("style.md")[:2000],
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
    )
    from app.pipeline.overlap import remember_chapter

    state = book.read_state()
    number = int(state.get("chapter_number") or 1)
    draft = book.read_chapter(number, draft=True)
    if not draft.strip():
        raise RuntimeError("Нет черновика для принятия")
    book.write_chapter(number, draft, draft=False)
    remember_chapter(book, number, draft)
    update_last_seen_from_text(book, number, draft)
    refresh_facts(book, number, draft)
    append_timeline(book, number, draft)
    update_summary(book, number, draft)
    book.patch_state(chapter_number=number + 1, course_note="", stage="chapter")
    hints = missing_character_hints(book)
    return {"accepted": number, "next": number + 1, "hints": hints}
