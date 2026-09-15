"""Книги на диске: markdown + state.json."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECTS_DIR, ensure_dirs

ARTIFACTS = (
    "card.md",
    "bible.md",
    "plan.md",
    "style.md",
    "timeline.md",
    "facts.md",
)

STAGES = ("card", "style", "bible", "plan", "chapter")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(title: str) -> str:
    text = title.strip().lower()
    text = re.sub(r"[^\w\s-]+", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    text = text[:48] or "kniga"
    return f"{text}-{uuid.uuid4().hex[:6]}"


def default_state(project_id: str, title: str) -> dict[str, Any]:
    return {
        "id": project_id,
        "title": title,
        "stage": "card",
        "chapter_number": 1,
        "approved": {"card": False, "style": False, "bible": False, "plan": False},
        "canon": True,
        "is_original": True,
        "adult": True,
        "last_seen": {},
        "characters": [],
        "course_note": "",
        "missing_hints": [],
        "summary": "",
        "created_at": _now(),
        "updated_at": _now(),
    }


class BookProject:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.id = root.name

    @property
    def chapters_dir(self) -> Path:
        return self.root / "chapters"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def sqlite_path(self) -> Path:
        return self.root / "overlap.sqlite"

    def read_state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = _now()
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def patch_state(self, **fields: Any) -> dict[str, Any]:
        state = self.read_state()
        state.update(fields)
        self.write_state(state)
        return state

    def read_text(self, name: str) -> str:
        path = self.root / name
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write_text(self, name: str, content: str) -> None:
        (self.root / name).write_text(content.replace("\r\n", "\n"), encoding="utf-8")

    def chapter_path(self, number: int, draft: bool = False) -> Path:
        suffix = "-draft.md" if draft else ".md"
        return self.chapters_dir / f"{number:03d}{suffix}"

    def read_chapter(self, number: int, draft: bool = False) -> str:
        path = self.chapter_path(number, draft=draft)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write_chapter(self, number: int, content: str, draft: bool = False) -> None:
        self.chapters_dir.mkdir(parents=True, exist_ok=True)
        path = self.chapter_path(number, draft=draft)
        text = content.replace("\r\n", "\n")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        if path.read_text(encoding="utf-8") != text:
            raise RuntimeError(f"Не удалось записать {path.name}")

    def last_finished_chapters(self, count: int = 2) -> list[tuple[int, str]]:
        state = self.read_state()
        current = int(state.get("chapter_number") or 1)
        out: list[tuple[int, str]] = []
        for num in range(current - 1, 0, -1):
            text = self.read_chapter(num, draft=False)
            if text.strip():
                out.append((num, text))
            if len(out) >= count:
                break
        out.reverse()
        return out

    def public_dict(self) -> dict[str, Any]:
        state = self.read_state()
        return {
            **state,
            "files": {name: self.read_text(name) for name in ARTIFACTS},
            "draft": self.read_chapter(int(state.get("chapter_number") or 1), draft=True),
            "hints": state.get("missing_hints") or [],
        }


def list_projects() -> list[dict[str, Any]]:
    ensure_dirs()
    items: list[dict[str, Any]] = []
    for path in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_dir() or not (path / "state.json").exists():
            continue
        book = BookProject(path)
        state = book.read_state()
        items.append(
            {
                "id": state.get("id") or path.name,
                "title": state.get("title") or path.name,
                "stage": state.get("stage"),
                "chapter_number": state.get("chapter_number"),
                "updated_at": state.get("updated_at"),
            }
        )
    return items


def create_project(title: str) -> BookProject:
    ensure_dirs()
    title = (title or "").strip() or "Новая книга"
    project_id = slugify(title)
    root = PROJECTS_DIR / project_id
    root.mkdir(parents=False)
    (root / "chapters").mkdir()
    book = BookProject(root)
    book.write_state(default_state(project_id, title))
    for name in ARTIFACTS:
        book.write_text(name, "")
    return book


def get_project(project_id: str) -> BookProject:
    ensure_dirs()
    if not re.fullmatch(r"[\w\-]+", project_id, flags=re.UNICODE):
        raise FileNotFoundError(project_id)
    root = PROJECTS_DIR / project_id
    if not root.is_dir() or not (root / "state.json").exists():
        raise FileNotFoundError(project_id)
    return BookProject(root)


def delete_project(project_id: str) -> None:
    book = get_project(project_id)
    shutil.rmtree(book.root)
