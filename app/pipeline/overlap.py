from app.storage.embeddings import OVERLAP_THRESHOLD, find_overlaps, store_chapter_embeddings
from app.storage.project import BookProject


def check_draft_overlap(book: BookProject, draft: str, chapter: int) -> list[dict]:
    return find_overlaps(
        book.sqlite_path,
        draft,
        threshold=OVERLAP_THRESHOLD,
        exclude_chapter=chapter,
    )


def remember_chapter(book: BookProject, chapter: int, text: str) -> int:
    return store_chapter_embeddings(book.sqlite_path, chapter, text)
