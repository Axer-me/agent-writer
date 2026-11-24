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
    "drama.md",
)

STAGES = ("card", "style", "drama", "bible", "plan", "chapter")


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
        "approved": {"card": False, "style": False, "drama": False, "bible": False, "plan": False},
        "stale": {"drama": False, "bible": False, "plan": False},
        "book_size": "medium",
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

    def contract_path(self, number: int) -> Path:
        return self.chapters_dir / f"{number:03d}-contract.md"

    def read_contract(self, number: int) -> str:
        path = self.contract_path(number)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write_contract(self, number: int, content: str) -> None:
        self.chapters_dir.mkdir(parents=True, exist_ok=True)
        path = self.contract_path(number)
        text = content.replace("\r\n", "\n")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

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

    def chapter_catalog(self) -> list[dict[str, Any]]:
        """Строки состава до потолка размера: статус, слова и даты файла. Текста глав здесь нет."""
        from app.pipeline.plan import _store
        from app.pipeline.scale import CHAPTER_CAP

        titles = {
            int(row["number"]): (row.get("title") or "")
            for row in _store(self).plan_chapters()
        }
        current = int(self.read_state().get("chapter_number") or 1)
        catalog: list[dict[str, Any]] = []
        for number in range(1, CHAPTER_CAP + 1):
            accepted = self.read_chapter(number, draft=False).strip()
            draft = self.read_chapter(number, draft=True).strip()
            if accepted:
                status = "accepted"
                text = accepted
                path = self.chapter_path(number, draft=False)
            elif draft:
                status = "draft"
                text = draft
                path = self.chapter_path(number, draft=True)
            else:
                status = "empty"
                text = ""
                path = None
            created, updated = _file_times(path)
            catalog.append(
                {
                    "number": number,
                    "title": titles.get(number, ""),
                    "status": status,
                    "words": len(text.split()) if text else 0,
                    "created_at": created,
                    "updated_at": updated,
                    "current": number == current,
                }
            )
        return catalog

    def book_times(self) -> tuple[str, str]:
        state = self.read_state()
        folder = self.root.stat()
        created = state.get("created_at") or _local_stamp(folder.st_ctime)
        updated = state.get("updated_at") or _local_stamp(folder.st_mtime)
        return created, updated

    def public_dict(self) -> dict[str, Any]:
        state = self.read_state()
        number = int(state.get("chapter_number") or 1)
        draft = self.read_chapter(number, draft=True)
        accepted = self.read_chapter(number - 1, draft=False) if number > 1 else ""
        created, updated = self.book_times()
        return {
            **state,
            "created_at": created,
            "updated_at": updated,
            "files": {name: self.read_text(name) for name in ARTIFACTS},
            "draft": draft,
            "accepted_text": accepted,
            "hints": state.get("missing_hints") or [],
            "chapter_index": self.chapter_catalog(),
            **_story_snapshot(self),
        }


def _file_times(path: Path | None) -> tuple[str, str]:
    if path is None or not path.exists():
        return "", ""
    stamp = path.stat()
    return _local_stamp(stamp.st_ctime), _local_stamp(stamp.st_mtime)


def _local_stamp(timestamp: float) -> str:
    moment = datetime.fromtimestamp(timestamp, timezone.utc).astimezone()
    return moment.isoformat(timespec="seconds")


def _story_snapshot(book: BookProject) -> dict[str, Any]:
    from app.pipeline.plan import _store

    return _store(book).snapshot()


def list_projects() -> list[dict[str, Any]]:
    ensure_dirs()
    items: list[dict[str, Any]] = []
    for path in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_dir() or not (path / "state.json").exists():
            continue
        book = BookProject(path)
        state = book.read_state()
        created, updated = book.book_times()
        items.append(
            {
                "id": state.get("id") or path.name,
                "title": state.get("title") or path.name,
                "stage": state.get("stage"),
                "chapter_number": state.get("chapter_number"),
                "updated_at": updated,
                "created_at": created,
            }
        )
    return items


def create_project(title: str, book_size: str = "medium") -> BookProject:
    from app.pipeline.scale import normalize_size

    ensure_dirs()
    title = (title or "").strip() or "Новая книга"
    project_id = slugify(title)
    root = PROJECTS_DIR / project_id
    root.mkdir(parents=False)
    (root / "chapters").mkdir()
    book = BookProject(root)
    state = default_state(project_id, title)
    state["book_size"] = normalize_size(book_size)
    book.write_state(state)
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
