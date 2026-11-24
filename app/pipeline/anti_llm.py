from __future__ import annotations

import re

from app.llm.router import complete_with_continue
from app.pipeline.prompts import BIBLE_NAMES, HUMAN_PROSE, KEEP_LENGTH, pack_priority, word_count

KEEP_RATIO = 0.90
_MARK = re.compile(
    r"не просто|почувствовал смесь|это изменило всё|вихрь эмоций|ткань реальности|стоит отметить|важно понимать",
    re.IGNORECASE,
)


def split_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", (text or "").strip()) if part.strip()]


def marked_indexes(text: str) -> list[int]:
    return [index for index, paragraph in enumerate(split_paragraphs(text)) if _MARK.search(paragraph)]


def splice_paragraphs(text: str, replacements: dict[int, str]) -> str:
    parts = split_paragraphs(text)
    for index, new in replacements.items():
        if not (0 <= index < len(parts)):
            continue
        if word_count(new) < max(8, int(word_count(parts[index]) * 0.8)):
            continue
        parts[index] = new.strip()
    return "\n\n".join(parts)


def _replacements(answer: str) -> dict[int, str]:
    current: int | None = None
    buffer: list[str] = []
    found: dict[int, str] = {}

    def flush() -> None:
        if current is not None:
            found[current] = "\n".join(buffer).strip()

    for raw in (answer or "").splitlines():
        match = re.match(r"^###\s+(\d+)\s*$", raw.strip())
        if match:
            flush()
            current = int(match.group(1))
            buffer = []
        elif current is not None:
            buffer.append(raw)
    flush()
    return found


_COMMON = frozenset(
    """
    что чтобы только когда потом снова через теперь словно будто который которая которые
    этого этом было были его она они ещё уже даже если или как так все всё для при над под
    без про это том там тут где кто чем того этой этот эта эти себя него неё стал стала
    были нет ещё один одна одни него
    """.split()
)
_ANACHRONISM = ("керосин", "антибиотик", "пулемет", "пулемёт", "автомат", "телефон", "бензин")


def _distance_one(left: str, right: str) -> bool:
    if not left or not right or left[0] != right[0] or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    short, long = (left, right) if len(left) < len(right) else (right, left)
    shift = 0
    for index, char in enumerate(short):
        if char != long[index + shift]:
            if shift:
                return False
            shift = 1
            if char != long[index + shift]:
                return False
    return True


def hot_words(text: str) -> list[str]:
    """Слова чаще трёх раз на тысячу и не реже четырёх раз во всей главе."""
    words = re.findall(r"[а-яё]{4,}", (text or "").lower())
    total = max(1, len((text or "").split()))
    counts: dict[str, int] = {}
    for word in words:
        if word in _COMMON:
            continue
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts, key=lambda word: counts[word], reverse=True)
    return [word for word in ranked if counts[word] >= 4 and counts[word] / total * 1000 > 3][:12]


def _era_year(*parts: str) -> int | None:
    found = re.findall(r"\b(1[0-9]{3})\b", " ".join(parts))
    return int(found[0]) if found else None


_SOFT_BEFORE_CONSONANT = re.compile(r"ьел|ьль|ь(?=[бвгджзклмнпрстфхцчшщ])")


def _frequent_words(text: str) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for word in re.findall(r"[а-яё]{4,}", (text or "").lower()):
        counts[word] = counts.get(word, 0) + 1
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)


def _garbled(token: str, frequent: list[tuple[str, int]]) -> bool:
    if _SOFT_BEFORE_CONSONANT.search(token):
        return True
    for word, count in frequent:
        if count < 2 or token == word or token[:5] == word[:5]:
            continue
        if _distance_one(token, word):
            return True
    return False


def editor_indexes(text: str, contract: str = "", world: str = "") -> list[int]:
    """Абзацы с клише, частым словом, опечаткой имени или словом не из эпохи."""
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return []
    hot = set(hot_words(text))
    frequent = _frequent_words(text)
    names = [name.lower() for name in participant_names(contract)]
    year = _era_year(world)
    banned = _ANACHRONISM if year is not None and year < 1860 else ()
    scored: list[tuple[int, int]] = []
    marked = set(marked_indexes(text))
    for index, paragraph in enumerate(paragraphs):
        low = paragraph.lower()
        score = 3 if index in marked else 0
        score += sum(low.count(word) for word in hot)
        if any(word in low for word in banned):
            score += 3
        for token in re.findall(r"[а-яё]{5,}", low):
            if _garbled(token, frequent) or any(
                token != name and _distance_one(token, name) for name in names
            ):
                score += 3
                break
        if score:
            scored.append((score, index))
    scored.sort(reverse=True)
    chosen = sorted(index for _score, index in scored[:16])
    return chosen


def de_llm(
    prose: str,
    style_md: str = "",
    contract: str = "",
    facts: str = "",
    world: str = "",
    *,
    always: bool = False,
) -> str:
    indexes = editor_indexes(prose, contract, world)
    if not indexes and always:
        paragraphs = split_paragraphs(prose)
        if paragraphs:
            mid = len(paragraphs) // 2
            indexes = sorted({0, mid, len(paragraphs) - 1})
    if not indexes:
        return prose
    current = prose
    for start in range(0, len(indexes), 8):
        current = _repair_paragraphs(
            current, indexes[start : start + 8], style_md, contract, facts, world
        )
    return current


def _repair_paragraphs(
    prose: str,
    indexes: list[int],
    style_md: str,
    contract: str,
    facts: str,
    world: str,
) -> str:
    chunks = split_paragraphs(prose)
    payload = "\n\n".join(f"### {index}\n{chunks[index]}" for index in indexes if index < len(chunks))
    if not payload:
        return prose
    repeated = ", ".join(hot_words(prose)[:8]) or "(нет)"
    year = _era_year(world)
    result = complete_with_continue(
        "anti_llm",
        [
            {
                "role": "system",
                "content": f"""Перепиши только переданные абзацы. Русский.
{HUMAN_PROSE}
{KEEP_LENGTH}
{BIBLE_NAMES}
Убери клише, опечатки и слова не из эпохи {year or "листа мира"}.
Слова, которые повторяются слишком часто: {repeated}. Часть повторов замени, но человека не теряй: имя остаётся, если без него неясно, кто действует.
Не меняй события, факты и порядок. Не сокращай абзац.
Верни каждый абзац отдельным блоком: строка ### N и текст. Остальную главу не пиши.""",
            },
            {
                "role": "user",
                "content": pack_priority(
                    [
                        "Контракт, его нельзя нарушать:\n"
                        + (contract or "(контракта нет — сохрани сюжет и имена)"),
                        "Факты, которые нельзя изменить:\n" + (facts or "(нет отдельного списка)"),
                        "Абзацы:\n" + payload,
                    ],
                    ["Стиль:\n" + (style_md[:2000] or "(нет образца)")],
                ),
            },
        ],
        max_tokens=2200,
        temperature=0.7,
        max_rounds=2,
    )
    if not result.ok:
        return prose
    out = splice_paragraphs(prose, _replacements(result.text))
    if not keeps_story(prose, out, contract):
        return prose
    return out


def keeps_story(source: str, rewritten: str, contract: str = "") -> bool:
    if word_count(rewritten) < int(word_count(source) * KEEP_RATIO):
        return False
    if not (contract or "").strip():
        return True
    source_low = source.lower()
    rewritten_low = rewritten.lower()
    for name in participant_names(contract):
        token = name.split()[0].lower()
        if len(token) >= 3 and token in source_low and token not in rewritten_low:
            return False
    change = chapter_change(contract)
    words = re.findall(r"[А-Яа-яA-Za-zЁё]{6,}", change)[:6]
    if words and not any(word.lower() in rewritten_low for word in words):
        return False
    return True


def participant_names(contract: str) -> list[str]:
    header = contract.split("###", 1)[0]
    match = re.search(r"(?m)^Участники:\s*(.+)$", header)
    if not match:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;]", match.group(1)):
        tokens = re.findall(r"[А-ЯA-ZЁ][а-яa-zё-]{2,}", part.strip())
        if not tokens:
            continue
        name = " ".join(tokens[:2])
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def chapter_change(contract: str) -> str:
    header = contract.split("###", 1)[0]
    match = re.search(r"(?m)^Изменение:\s*(.+)$", header)
    return match.group(1).strip() if match else ""
