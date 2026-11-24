"""Размер книги задаёт диапазон глав. Точное число выбирает сюжет."""

from __future__ import annotations

CHAPTER_CAP = 100

SIZES: dict[str, tuple[str, int, int]] = {
    "mini": ("Мини", 8, 25),
    "medium": ("Среднее", 25, 60),
    "large": ("Большое", 60, 100),
}

_ALIASES = {"мини": "mini", "среднее": "medium", "большое": "large"}


def normalize_size(value: str | None) -> str:
    key = _ALIASES.get((value or "").strip().lower(), (value or "").strip().lower())
    return key if key in SIZES else "medium"


def size_band(size: str | None) -> tuple[str, int, int]:
    return SIZES[normalize_size(size)]


def book_band(book) -> tuple[str, int, int]:
    return size_band((book.read_state() or {}).get("book_size"))
