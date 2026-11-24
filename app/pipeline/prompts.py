HUMAN_PROSE = """
Пиши по-русски как живой прозаик, не как нейросеть.
Запрещено: ровные абзацы одной длины; конструкции «не просто X, а Y»; мораль сцены в конце;
объявлять эмоции в лоб («он почувствовал смесь…», «это изменило всё»); канцелярит и кальки;
пустые метафоры («вихрь эмоций», «ткань реальности»); герои, которые объясняют сюжет репликами;
слова «стоит отметить», «важно понимать»; идеально вежливые резюме.
Нужны неровный ритм, конкретные детали, характер в поступках и речи.
Пиши действие и одну конкретную деталь места или предмета. Быт бери из задания, листа мира и карточки, не выдумывай второй.
Диалог встроен в сцену, а не заменяет её.
""".strip()

CANON_RULE = """
Канон вселенной соблюдать, пока в карточке явно не сказано AU.
Для оригинала мир задают карточка и библия.
Для чужой вселенной подтверждённые заметки и карточка важнее библии.
Факт, которого нет в заметках и карточке, не выдавай за канон и не бери из памяти о франшизе.
Имена, титулы, родство, королевства, школы — только из этих материалов.
Не подставлять чужие титулы (пример: Цири — не дочь короля Ривии; Ривия связана с Геральтом).
Новые повороты — да. Ломать канон — только если в карточке явно AU, и только в пределах этой AU-пометки.
""".strip()

BIBLE_NAMES = (
    "Имена, титулы, города, родство сверяй с библией и карточкой. "
    "Если в библии нет — не бери из памяти о франшизе."
)

KEEP_LENGTH = (
    "Не сокращай текст. Не делай конспект. Не выкидывай быт и паузы ради «динамики»."
)


def word_count(text: str) -> int:
    return len((text or "").split())


def comment_block(comment: str | None) -> str:
    comment = (comment or "").strip()
    if not comment:
        return ""
    return f"\n\nЗамечания пользователя (учти обязательно):\n{comment}\n"


def pack(*parts: str, limit: int = 36000) -> str:
    cleaned = [p.strip() for p in parts if p and str(p).strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return pack_priority([cleaned[0]], limit=limit)
    return pack_priority([cleaned[-1]], cleaned[:-1], limit=limit)


def pack_priority(
    required: list[str],
    optional: list[str] | None = None,
    limit: int = 36000,
) -> str:
    """Обязательные блоки остаются целиком, пока помещаются. Справочные режутся первыми.

    Если обязательная часть длиннее лимита, сокращается её начало: конец события сохраняется.
    """
    required_parts = [str(p).strip() for p in required if p and str(p).strip()]
    optional_parts = [str(p).strip() for p in (optional or []) if p and str(p).strip()]
    required_parts = _fit_required(required_parts, limit)
    base = "\n\n".join(required_parts)
    budget = limit - len(base) - (2 if required_parts else 0)
    chosen: list[str] = []
    for part in optional_parts:
        sep = 2 if chosen else 0
        room = budget - sep
        if room <= 80:
            break
        if len(part) + sep <= budget:
            chosen.append(part)
            budget -= len(part) + sep
            continue
        snippet = part[: room - len(_OPTIONAL_NOTE)].rstrip()
        if len(snippet) > 80:
            chosen.append(snippet + _OPTIONAL_NOTE)
        break
    return "\n\n".join(chosen + required_parts).strip()


_OPTIONAL_NOTE = "\n\n[...справочный фрагмент сокращён]"
_REQUIRED_NOTE = "[...начало сокращено]\n"


def _fit_required(parts: list[str], limit: int) -> list[str]:
    parts = list(parts)
    for _ in range(24):
        joined = "\n\n".join(parts)
        if len(joined) <= limit or not parts:
            return parts
        idx = max(range(len(parts)), key=lambda i: len(parts[i]))
        overflow = len(joined) - limit
        target = max(120, len(parts[idx]) - overflow)
        updated = _shrink_to_tail(parts[idx], target)
        if updated == parts[idx]:
            return parts
        parts[idx] = updated
    return parts


def _shrink_to_tail(part: str, target: int) -> str:
    if len(part) <= target:
        return part
    tail = max(0, target - len(_REQUIRED_NOTE))
    return _REQUIRED_NOTE + part[-tail:]
