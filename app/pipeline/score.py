"""Оценка готовой главы без нового вызова модели."""

from __future__ import annotations

import re
from collections import Counter

from app.pipeline.anti_llm import participant_names


def chapter_report(text: str, needles: list[str] | None = None, contract: str = "") -> dict:
    words = (text or "").split()
    total = max(1, len(words))
    counts = Counter(re.findall(r"[а-яё]{4,}", (text or "").lower()))
    hot = [word for word, count in counts.items() if count >= 4 and count / total * 1000 > 3]
    sentences = [part for part in re.split(r"(?<=[.!?…])\s+", text or "") if part.strip()]
    lengths = [len(part.split()) for part in sentences] or [0]
    missing = [item for item in (needles or []) if item.lower() not in (text or "").lower()]
    absent = []
    low = (text or "").lower()
    for name in participant_names(contract):
        token = name.split()[0].lower()
        if len(token) >= 3 and token not in low:
            absent.append(name)
    return {
        "hot": sorted(hot),
        "sentence_avg": round(sum(lengths) / len(lengths), 1),
        "missing_needles": missing,
        "absent_names": absent,
    }
