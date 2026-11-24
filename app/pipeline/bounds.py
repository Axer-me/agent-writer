"""Глава не имеет права уйти дальше своего пункта плана."""

from __future__ import annotations

import re

# Сильный скачок времени. «Утро» и «следующая сцена» сюда не входят.
# Неделя разбирается отдельно: обещание «вернётся через неделю» часы сюжета не переносит.
_SKIPS: tuple[tuple[re.Pattern[str], re.Pattern[str], str], ...] = (
    (
        re.compile(r"прош(?:ёл|ел|ла|ло|ли)\s+(?:целый\s+)?месяц|через\s+месяц|спустя\s+месяц|месяц\s+спустя", re.I),
        re.compile(r"месяц", re.I),
        "месяц",
    ),
    (
        re.compile(r"прош(?:ёл|ел|ла|ло|ли)\s+год|через\s+год|спустя\s+год", re.I),
        re.compile(r"(?:через|спустя|целый|прош(?:ёл|ел|ла|ло|ли))\s+год", re.I),
        "год",
    ),
    (
        re.compile(
            r"дни\s+шли|день\s+за\s+дн[её]м|прошло\s+несколько\s+дней|через\s+несколько\s+дней|дни\s+тянулись",
            re.I,
        ),
        re.compile(r"несколько\s+дней|дни\s+шли|день\s+за\s+дн", re.I),
        "несколько дней",
    ),
)
_WEEK_ALLOWED = re.compile(r"недел[юяеи]", re.I)
_WEEK_HARD = re.compile(
    r"прош(?:ёл|ел|ла|ло|ли)\s+недел|недели\s+спустя|спустя\s+недел",
    re.I,
)
_WEEK_SOFT = re.compile(r"через\s+недел", re.I)
_PROMISE = re.compile(r"верн|обеща|потом|позже|завтра|жди", re.I)
_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE.findall(text or "") if part.strip()]


def _week_jump(text: str) -> bool:
    """«Прошла неделя» переносит часы. «Обещает вернуться через неделю» — нет."""
    for sentence in _sentences(text):
        if _WEEK_HARD.search(sentence):
            return True
        if _WEEK_SOFT.search(sentence) and not _PROMISE.search(sentence):
            return True
    return False


def plan_bound_issues(text: str, plan_text: str) -> list[str]:
    """Скачок, которого нет в пункте этой главы."""
    issues: list[str] = []
    plan = plan_text or ""
    body = text or ""
    for found, allowed, label in _SKIPS:
        if found.search(body) and not allowed.search(plan):
            issues.append(f"В тексте есть скачок на {label}, а в пункте этой главы его нет.")
    if _week_jump(body) and not _WEEK_ALLOWED.search(plan):
        issues.append("В тексте есть скачок на неделю, а в пункте этой главы его нет.")
    return issues


def bound_block_reason(text: str, plan_text: str) -> str:
    issues = plan_bound_issues(text, plan_text)
    if not issues:
        return ""
    return "Глава уходит дальше пункта плана. " + " ".join(issues) + " Поправьте черновик или запустите главу снова."


_PLACE_STOP = {
    "место",
    "время",
    "сцена",
    "глава",
    "герой",
    "героя",
    "ночью",
    "утром",
    "вечером",
}
_WORD = re.compile(r"[А-Яа-яЁёA-Za-z]{5,}")


def _field(contract: str, label: str) -> str:
    header = (contract or "").split("###", 1)[0]
    match = re.search(rf"(?m)^{label}:\s*(.+)$", header)
    return match.group(1).strip() if match else ""


def _stems(phrase: str) -> list[str]:
    stems = []
    for word in _WORD.findall(phrase or ""):
        if word.lower() in _PLACE_STOP:
            continue
        stems.append(word.lower()[:4])
    return stems


def contract_issues(text: str, contract: str, dead_names: list[str] | None = None) -> list[str]:
    """Место, участники и запрет контракта. Пустой контракт ничего не блокирует."""
    if not (contract or "").strip():
        return []
    issues: list[str] = []
    body = text or ""
    opening = body[:2500].lower()
    place = _stems(_field(contract, "Место и время"))
    if place and not any(stem in opening for stem in place):
        issues.append("В начале главы нет места из контракта.")
    from app.pipeline.anti_llm import participant_names

    low = body.lower()
    missing = []
    for name in participant_names(contract):
        token = name.split()[0].lower()
        if len(token) >= 3 and token not in low:
            missing.append(name)
    if missing:
        issues.append("В тексте нет участников контракта: " + ", ".join(missing) + ".")
    forbidden = _field(contract, "Нельзя раскрывать")
    for clause in re.split(r"[.;]", forbidden):
        words = [word.lower() for word in _WORD.findall(clause) if word.lower() not in _PLACE_STOP]
        if len(words) < 2:
            continue
        hits = [word for word in words if word in low]
        if len(hits) >= 2:
            issues.append("Глава раскрывает то, что контракт запрещает: " + clause.strip() + ".")
            break
    from app.pipeline.select import mentioned

    for name in dead_names or []:
        if name and mentioned(low, name):
            issues.append(f"В тексте есть имя со статусом «мёртв»: {name}.")
    return issues


def acceptance_block(text: str, plan_text: str, contract: str = "", dead_names: list[str] | None = None) -> str:
    issues = plan_bound_issues(text, plan_text) + contract_issues(text, contract, dead_names)
    if not issues:
        return ""
    return "Глава не сходится с планом или контрактом. " + " ".join(issues) + " Поправьте черновик или запустите главу снова."
