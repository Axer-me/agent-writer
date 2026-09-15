"""Локальные эмбеддинги multilingual-e5-small + SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

MODEL_NAME = "intfloat/multilingual-e5-small"
OVERLAP_THRESHOLD = 0.92

_model = None


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME, device=_device())
    return _model


def embed_passages(texts: list[str]) -> np.ndarray:
    model = get_model()
    cleaned = [f"passage: {t.strip()}" for t in texts if t.strip()]
    if not cleaned:
        return np.zeros((0, 384), dtype=np.float32)
    vectors = model.encode(
        cleaned,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype=np.float32)


def split_chunks(text: str, max_chars: int = 700) -> list[str]:
    parts = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for part in parts:
        if len(part) > max_chars:
            if buf:
                chunks.append(buf.strip())
                buf = ""
            for i in range(0, len(part), max_chars):
                chunks.append(part[i : i + max_chars].strip())
            continue
        if len(buf) + len(part) + 2 > max_chars:
            if buf:
                chunks.append(buf.strip())
            buf = part
        else:
            buf = (buf + "\n\n" + part).strip() if buf else part
    if buf:
        chunks.append(buf.strip())
    return chunks


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            text TEXT NOT NULL,
            vector BLOB NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def store_chapter_embeddings(sqlite_path: Path, chapter: int, text: str) -> int:
    chunks = split_chunks(text)
    if not chunks:
        return 0
    vectors = embed_passages(chunks)
    conn = _connect(sqlite_path)
    conn.execute("DELETE FROM chunks WHERE chapter = ?", (chapter,))
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        conn.execute(
            "INSERT INTO chunks (chapter, ordinal, text, vector) VALUES (?, ?, ?, ?)",
            (chapter, i, chunk, vector.tobytes()),
        )
    conn.commit()
    conn.close()
    return len(chunks)


def find_overlaps(
    sqlite_path: Path,
    text: str,
    *,
    threshold: float = OVERLAP_THRESHOLD,
    exclude_chapter: int | None = None,
) -> list[dict]:
    chunks = split_chunks(text)
    if not chunks:
        return []
    query_vecs = embed_passages(chunks)
    conn = _connect(sqlite_path)
    rows = conn.execute(
        "SELECT chapter, ordinal, text, vector FROM chunks"
    ).fetchall()
    conn.close()
    if not rows:
        return []

    stored_vecs = []
    meta = []
    for chapter, ordinal, stored_text, blob in rows:
        if exclude_chapter is not None and chapter == exclude_chapter:
            continue
        vec = np.frombuffer(blob, dtype=np.float32)
        stored_vecs.append(vec)
        meta.append((chapter, ordinal, stored_text))
    if not stored_vecs:
        return []
    matrix = np.stack(stored_vecs)
    hits: list[dict] = []
    for i, q in enumerate(query_vecs):
        scores = matrix @ q
        best = int(np.argmax(scores))
        score = float(scores[best])
        if score >= threshold:
            chapter, ordinal, stored_text = meta[best]
            hits.append(
                {
                    "query_index": i,
                    "query_preview": chunks[i][:240],
                    "chapter": chapter,
                    "ordinal": ordinal,
                    "score": round(score, 4),
                    "stored_preview": stored_text[:240],
                }
            )
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits


def cosine_pair(a: str, b: str) -> float:
    vecs = embed_passages([a, b])
    if len(vecs) < 2:
        return 0.0
    return float(vecs[0] @ vecs[1])


def selftest() -> dict:
    similar_a = (
        "Герат вошёл в корчму, стряхнул снег с плаща и сел к стойке. "
        "Хозяйка налила эль, не задавая лишних вопросов."
    )
    similar_b = (
        "Герат зашёл в корчму, стряхнул снег с плаща и сел у стойки. "
        "Хозяйка налила ему эль и не стала задавать лишних вопросов."
    )
    different = (
        "На рассвете караван ушёл в пустыню. Верблюды шли цепочкой, "
        "а проводник молчал три дня подряд."
    )
    high = cosine_pair(similar_a, similar_b)
    low = cosine_pair(similar_a, different)
    ok = high > 0.95 and high > low + 0.08
    return {
        "ok": ok,
        "device": _device(),
        "model": MODEL_NAME,
        "similar_score": round(high, 4),
        "different_score": round(low, 4),
    }
