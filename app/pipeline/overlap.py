from app.storage.embeddings import OVERLAP_THRESHOLD, find_overlaps, split_chunks, store_chapter_embeddings
from app.storage.project import BookProject


def neighboring_duplicates(text: str) -> list[dict]:
    """Одинаковые соседние абзацы внутри главы. Без модели."""
    import re

    chunks = [part.strip() for part in re.split(r"\n\s*\n", text or "") if part.strip()]
    hits: list[dict] = []
    for index in range(len(chunks) - 1):
        left = " ".join(chunks[index].split())
        right = " ".join(chunks[index + 1].split())
        if len(left) < 40 or left != right:
            continue
        hits.append(
            {
                "query_preview": chunks[index][:240],
                "chapter": "эта глава",
                "score": 1.0,
                "stored_preview": chunks[index + 1][:240],
            }
        )
    return hits


def vector_repeats(text: str, threshold: float = OVERLAP_THRESHOLD) -> list[dict]:
    """Похожие куски одной главы. Тот же порог, что и у прошлых глав."""
    from app.storage.embeddings import embed_passages

    chunks = split_chunks(text or "")
    if len(chunks) < 2:
        return []
    vectors = embed_passages(chunks)
    hits: list[dict] = []
    seen: set[int] = set()
    for left in range(len(chunks)):
        for right in range(left + 1, len(chunks)):
            score = float(vectors[left] @ vectors[right])
            if score < threshold or left in seen:
                continue
            seen.add(left)
            hits.append(
                {
                    "query_preview": chunks[left][:240],
                    "chapter": "эта глава",
                    "score": round(score, 4),
                    "stored_preview": chunks[right][:240],
                }
            )
            break
    return hits


def check_draft_overlap(book: BookProject, draft: str, chapter: int) -> list[dict]:
    hits = neighboring_duplicates(draft)
    try:
        hits.extend(vector_repeats(draft))
    except Exception:
        pass
    hits.extend(
        find_overlaps(
            book.sqlite_path,
            draft,
            threshold=OVERLAP_THRESHOLD,
            exclude_chapter=chapter,
        )
    )
    return hits


def remember_chapter(book: BookProject, chapter: int, text: str) -> int:
    return store_chapter_embeddings(book.sqlite_path, chapter, text)
