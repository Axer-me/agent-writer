"""Память сюжета: план, события, факты и окно арки. Не overlap.sqlite."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_FACT_MARK = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$")
_FACT_LABEL = re.compile(r"^\s*факт\s*[:—-]\s*(.+)$", re.IGNORECASE)
_FACT_SKIP = re.compile(r"^(?:старые факты|таймлайн|формат|уже есть)\b", re.IGNORECASE)
_EVENT_LINE = re.compile(
    r"^-\s*Глава\s+(\d+)\s*\|\s*(решение|рана|уход|знание)\s*\|\s*(.+?)\s*\|\s*(открыто|закрыто)\s*$",
    re.IGNORECASE,
)


class StoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "story.sqlite"

    def connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS plan_chapters (
                number INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                event TEXT NOT NULL DEFAULT '',
                deferral TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS story_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter INTEGER NOT NULL,
                kind TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fact_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                body TEXT NOT NULL,
                chapter INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS arc_windows (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                promise TEXT NOT NULL DEFAULT '',
                payoff_chapter INTEGER NOT NULL DEFAULT 0,
                start_chapter INTEGER NOT NULL DEFAULT 1,
                end_chapter INTEGER NOT NULL DEFAULT 5,
                steps TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                retries INTEGER NOT NULL DEFAULT 0,
                ok INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                latency_ms INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS canon_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                body TEXT NOT NULL,
                continuity TEXT NOT NULL DEFAULT 'заметка'
            );
            CREATE TABLE IF NOT EXISTS story_spine (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                finale TEXT NOT NULL DEFAULT '',
                act1 TEXT NOT NULL DEFAULT '',
                act2 TEXT NOT NULL DEFAULT '',
                act3 TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS story_drama (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                hero TEXT NOT NULL DEFAULT '',
                want TEXT NOT NULL DEFAULT '',
                belief TEXT NOT NULL DEFAULT '',
                cost TEXT NOT NULL DEFAULT '',
                becomes TEXT NOT NULL DEFAULT '',
                act1 TEXT NOT NULL DEFAULT '',
                act2 TEXT NOT NULL DEFAULT '',
                act3 TEXT NOT NULL DEFAULT '',
                world1 TEXT NOT NULL DEFAULT '',
                world2 TEXT NOT NULL DEFAULT '',
                world3 TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS move_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter INTEGER NOT NULL,
                tag TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS story_world (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                when_where TEXT NOT NULL DEFAULT '',
                daily TEXT NOT NULL DEFAULT '',
                first_hour TEXT NOT NULL DEFAULT '',
                hidden TEXT NOT NULL DEFAULT ''
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(plan_chapters)")}
        if "reveal" not in columns:
            conn.execute("ALTER TABLE plan_chapters ADD COLUMN reveal TEXT NOT NULL DEFAULT ''")
        drama_columns = {row[1] for row in conn.execute("PRAGMA table_info(story_drama)")}
        for name in ("world1", "world2", "world3"):
            if name not in drama_columns:
                conn.execute(
                    f"ALTER TABLE story_drama ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                )
        return conn

    @contextmanager
    def _db(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def import_legacy(self, plan_md: str, facts_md: str) -> None:
        with self._db() as conn:
            if not conn.execute("SELECT 1 FROM plan_chapters LIMIT 1").fetchone() and (plan_md or "").strip():
                from app.pipeline.plan import rows_from_plan_text

                for row in rows_from_plan_text(plan_md):
                    _upsert_plan(conn, row)
            if not conn.execute("SELECT 1 FROM fact_items LIMIT 1").fetchone():
                for body in fact_lines(facts_md):
                    conn.execute("INSERT INTO fact_items (body, chapter) VALUES (?, 0)", (body,))
            conn.commit()

    def plan_chapters(self) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT number, title, tags, event, deferral, reveal FROM plan_chapters ORDER BY number"
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_plan(self, rows: list[dict[str, Any]]) -> None:
        with self._db() as conn:
            conn.execute("DELETE FROM plan_chapters")
            for row in rows:
                _upsert_plan(conn, row)
            conn.commit()

    def upsert_plan(self, rows: list[dict[str, Any]]) -> None:
        with self._db() as conn:
            for row in rows:
                _upsert_plan(conn, row)
            conn.commit()

    def render_plan(self) -> str:
        parts = []
        for row in self.plan_chapters():
            parts.append(
                f"## Глава {row['number']}. {row['title']}\n"
                f"Теги: {row['tags']}\n"
                f"Кратко: {row['event']}".strip()
            )
        return "\n\n".join(parts).strip()

    def plan_chapter_text(self, number: int) -> str:
        with self._db() as conn:
            row = conn.execute(
                "SELECT number, title, tags, event, deferral, reveal FROM plan_chapters WHERE number = ?",
                (number,),
            ).fetchone()
        if not row:
            return ""
        item = dict(row)
        return (
            f"## Глава {item['number']}. {item['title']}\n"
            f"Теги: {item['tags']}\n"
            f"Кратко: {item['event']}"
        ).strip()

    def events(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._db() as conn:
            if status:
                rows = conn.execute(
                    "SELECT id, chapter, kind, body, status FROM story_events WHERE status = ? ORDER BY id",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, chapter, kind, body, status FROM story_events ORDER BY id"
                ).fetchall()
        return [dict(row) for row in rows]

    def apply_events(self, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not updates:
            return self.events()
        with self._db() as conn:
            for upd in updates:
                current = conn.execute(
                    """
                    SELECT id FROM story_events
                    WHERE chapter = ? AND kind = ? AND status = 'открыто'
                    ORDER BY id LIMIT 1
                    """,
                    (upd["chapter"], upd["kind"]),
                ).fetchone()
                if current:
                    conn.execute(
                        "UPDATE story_events SET body = ?, status = ? WHERE id = ?",
                        (upd["body"], upd["status"], current["id"]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO story_events (chapter, kind, body, status) VALUES (?, ?, ?, ?)",
                        (upd["chapter"], upd["kind"], upd["body"], upd["status"]),
                    )
            conn.commit()
        return self.events()

    def events_text(self, status: str | None = "открыто") -> str:
        lines = [
            f"- Глава {row['chapter']} | {row['kind']} | {row['body']} | {row['status']}"
            for row in self.events(status)
        ]
        return "\n".join(lines)

    def facts(self) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute("SELECT id, body, chapter FROM fact_items ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def replace_facts(self, bodies: list[str], chapter: int) -> None:
        cleaned = [body.strip() for body in bodies if body and body.strip()]
        if not cleaned:
            return
        with self._db() as conn:
            conn.execute("DELETE FROM fact_items")
            conn.executemany(
                "INSERT INTO fact_items (body, chapter) VALUES (?, ?)",
                [(body, chapter) for body in cleaned],
            )
            conn.commit()

    def facts_text(self) -> str:
        return "\n".join(f"- {row['body']}" for row in self.facts())

    def arc(self) -> dict[str, Any] | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT promise, payoff_chapter, start_chapter, end_chapter, steps FROM arc_windows WHERE id = 1"
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def clear_arc(self) -> None:
        with self._db() as conn:
            conn.execute("DELETE FROM arc_windows WHERE id = 1")
            conn.commit()

    def save_arc(self, arc: dict[str, Any]) -> None:
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO arc_windows (id, promise, payoff_chapter, start_chapter, end_chapter, steps)
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    promise = excluded.promise,
                    payoff_chapter = excluded.payoff_chapter,
                    start_chapter = excluded.start_chapter,
                    end_chapter = excluded.end_chapter,
                    steps = excluded.steps
                """,
                (
                    arc.get("promise") or "",
                    int(arc.get("payoff_chapter") or 0),
                    int(arc.get("start_chapter") or 1),
                    int(arc.get("end_chapter") or 5),
                    arc.get("steps") or "",
                ),
            )
            conn.commit()

    def arc_text(self) -> str:
        arc = self.arc()
        if not arc or not (arc.get("promise") or arc.get("steps")):
            return ""
        return (
            f"Обещание: {arc['promise']}\n"
            f"Выплата не раньше главы: {arc['payoff_chapter']}\n"
            f"Главы {arc['start_chapter']}–{arc['end_chapter']}:\n"
            f"{arc['steps']}"
        ).strip()

    def add_call(self, task: str, provider: str, model: str, retries: int, ok: bool, error: str, latency_ms: int) -> None:
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO llm_calls (task, provider, model, retries, ok, error, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (task, provider or "", model or "", int(retries or 0), 1 if ok else 0, (error or "")[:400], int(latency_ms or 0)),
            )

    def last_call(self) -> dict[str, Any]:
        with self._db() as conn:
            row = conn.execute(
                "SELECT task, provider, model, retries, ok, error, latency_ms FROM llm_calls ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else {}

    def replace_canon_notes(self, text: str) -> None:
        rows: list[tuple[str, str]] = []
        for raw in (text or "").splitlines():
            line = raw.strip().lstrip("-").strip()
            if len(line) < 3:
                continue
            continuity = "заметка"
            for label, name in (("книги:", "книги"), ("игры:", "игры"), ("сериал:", "сериал")):
                if line.lower().startswith(label):
                    continuity = name
                    line = line.split(":", 1)[1].strip()
                    break
            if line:
                rows.append((line, continuity))
        with self._db() as conn:
            conn.execute("DELETE FROM canon_notes")
            conn.executemany(
                "INSERT INTO canon_notes (body, continuity) VALUES (?, ?)",
                rows,
            )

    def canon_text(self) -> str:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT body, continuity FROM canon_notes ORDER BY id"
            ).fetchall()
        lines = []
        for row in rows:
            if row["continuity"] == "заметка":
                lines.append(row["body"])
            else:
                lines.append(f"{row['continuity']}: {row['body']}")
        return "\n".join(lines)

    def save_spine(self, spine: dict[str, Any]) -> None:
        if not spine:
            return
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO story_spine (id, finale, act1, act2, act3)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    finale = excluded.finale,
                    act1 = excluded.act1,
                    act2 = excluded.act2,
                    act3 = excluded.act3
                """,
                (
                    spine.get("finale") or "",
                    spine.get("act1") or "",
                    spine.get("act2") or "",
                    spine.get("act3") or "",
                ),
            )

    def spine(self) -> dict[str, str]:
        with self._db() as conn:
            row = conn.execute(
                "SELECT finale, act1, act2, act3 FROM story_spine WHERE id = 1"
            ).fetchone()
        if not row:
            return {"finale": "", "act1": "", "act2": "", "act3": ""}
        return dict(row)

    def save_drama(self, drama: dict[str, Any]) -> None:
        data = drama or {}
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO story_drama
                    (id, hero, want, belief, cost, becomes, act1, act2, act3, world1, world2, world3)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    hero = excluded.hero,
                    want = excluded.want,
                    belief = excluded.belief,
                    cost = excluded.cost,
                    becomes = excluded.becomes,
                    act1 = excluded.act1,
                    act2 = excluded.act2,
                    act3 = excluded.act3,
                    world1 = excluded.world1,
                    world2 = excluded.world2,
                    world3 = excluded.world3
                """,
                (
                    data.get("hero") or "",
                    data.get("want") or "",
                    data.get("belief") or "",
                    data.get("cost") or "",
                    data.get("becomes") or "",
                    data.get("act1") or "",
                    data.get("act2") or "",
                    data.get("act3") or "",
                    data.get("world1") or "",
                    data.get("world2") or "",
                    data.get("world3") or "",
                ),
            )

    def drama(self) -> dict[str, str]:
        with self._db() as conn:
            row = conn.execute(
                """
                SELECT hero, want, belief, cost, becomes, act1, act2, act3, world1, world2, world3
                FROM story_drama WHERE id = 1
                """
            ).fetchone()
        if not row:
            return {
                "hero": "",
                "want": "",
                "belief": "",
                "cost": "",
                "becomes": "",
                "act1": "",
                "act2": "",
                "act3": "",
                "world1": "",
                "world2": "",
                "world3": "",
            }
        return dict(row)

    def save_world(self, world: dict[str, Any]) -> None:
        data = world or {}
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO story_world (id, when_where, daily, first_hour, hidden)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    when_where = excluded.when_where,
                    daily = excluded.daily,
                    first_hour = excluded.first_hour,
                    hidden = excluded.hidden
                """,
                (
                    data.get("when_where") or "",
                    data.get("daily") or "",
                    data.get("first_hour") or "",
                    data.get("hidden") or "",
                ),
            )

    def world(self) -> dict[str, str]:
        with self._db() as conn:
            row = conn.execute(
                "SELECT when_where, daily, first_hour, hidden FROM story_world WHERE id = 1"
            ).fetchone()
        if not row:
            return {"when_where": "", "daily": "", "first_hour": "", "hidden": ""}
        return {key: str(value or "").replace("**", "").strip() for key, value in dict(row).items()}

    def save_reveals(self, reveals: dict[int, str]) -> None:
        if not reveals:
            return
        with self._db() as conn:
            for number, text in reveals.items():
                conn.execute(
                    "UPDATE plan_chapters SET reveal = ? WHERE number = ?",
                    ((text or "").strip(), int(number)),
                )

    def record_moves(self, chapter: int, tags: str) -> None:
        cleaned = [part.strip().lower() for part in (tags or "").split(",") if part.strip()]
        with self._db() as conn:
            conn.execute("DELETE FROM move_log WHERE chapter = ?", (chapter,))
            conn.executemany(
                "INSERT INTO move_log (chapter, tag) VALUES (?, ?)",
                [(chapter, tag) for tag in cleaned],
            )

    def move_warning(self, chapter: int) -> str:
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT chapter, tag FROM move_log
                WHERE chapter < ? AND chapter >= ?
                ORDER BY chapter
                """,
                (chapter, max(1, chapter - 5)),
            ).fetchall()
        seen: dict[str, list[int]] = {}
        for row in rows:
            seen.setdefault(row["tag"], [])
            if row["chapter"] not in seen[row["tag"]]:
                seen[row["tag"]].append(row["chapter"])
        lines = []
        for tag, chapters in seen.items():
            if len(chapters) >= 3:
                numbers = ", ".join(str(number) for number in chapters)
                lines.append(
                    f"Ход «{tag}» уже был в главах {numbers}. Не повторяй его, если пункт этой главы его не требует."
                )
        return "\n".join(lines)

    def snapshot(self) -> dict[str, Any]:
        return {
            "plan": self.plan_chapters(),
            "events": self.events(),
            "facts": self.facts(),
            "arc": self.arc(),
            "canon_notes": self.canon_text(),
            "spine": self.spine(),
            "drama": self.drama(),
            "world": self.world(),
            "last_call": self.last_call(),
        }


_FACT_META = re.compile(r"нет упомина|не упомина", re.IGNORECASE)
_FACT_SCHEMA = re.compile(
    r"^(?:деньги\s*:|рана\s*[:/]|где кто\s*:|окончание главы\s*:|день/время)",
    re.IGNORECASE,
)


def usable_facts(lines: list[str]) -> list[str]:
    """Убирает отчёт модели о дырке. Пустой результат значит «старые факты не трогать»."""
    kept: list[str] = []
    for line in lines:
        body = (line or "").strip()
        if not body or _FACT_META.search(body) or _FACT_SCHEMA.match(body):
            continue
        kept.append(body)
    return kept[:40]


def fact_lines(text: str) -> list[str]:
    """Строки фактов. Маркеры предпочтительны, короткие фразы без маркера тоже принимаются."""
    marked: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        match = _FACT_MARK.match(line) or _FACT_LABEL.match(line)
        if match and match.group(1).strip():
            marked.append(match.group(1).strip())
    if marked:
        return _unique_short(marked)
    loose: list[str] = []
    chunks = (text or "").splitlines() or [text or ""]
    for raw in chunks:
        piece = raw.strip().lstrip("#").strip()
        if not piece or piece.endswith(":") or _FACT_SKIP.match(piece):
            continue
        sentences = re.split(r"(?<=[.!?])\s+", piece) if len(piece) > 220 else [piece]
        for sentence in sentences:
            sentence = sentence.strip()
            if 12 <= len(sentence) <= 220:
                loose.append(sentence)
    return _unique_short(loose)[:40]


def _unique_short(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(line)
    return result


def parse_event_updates(text: str) -> list[dict[str, Any]]:
    updates = []
    for line in (text or "").splitlines():
        match = _EVENT_LINE.match(line.strip())
        if not match:
            continue
        updates.append(
            {
                "chapter": int(match.group(1)),
                "kind": match.group(2).lower(),
                "body": match.group(3).strip(),
                "status": match.group(4).lower(),
            }
        )
    return updates


def _upsert_plan(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    number = int(row["number"])
    from app.pipeline.scale import CHAPTER_CAP

    if not 1 <= number <= CHAPTER_CAP:
        return
    title = (row.get("title") or "").strip()
    tags = (row.get("tags") or "").strip()
    event = (row.get("event") or "").strip()
    deferral = (row.get("deferral") or "").strip()
    if "reveal" in row:
        conn.execute(
            """
            INSERT INTO plan_chapters (number, title, tags, event, deferral, reveal)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(number) DO UPDATE SET
                title = excluded.title,
                tags = excluded.tags,
                event = excluded.event,
                deferral = excluded.deferral,
                reveal = excluded.reveal
            """,
            (number, title, tags, event, deferral, (row.get("reveal") or "").strip()),
        )
        return
    conn.execute(
        """
        INSERT INTO plan_chapters (number, title, tags, event, deferral)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(number) DO UPDATE SET
            title = excluded.title,
            tags = excluded.tags,
            event = excluded.event,
            deferral = excluded.deferral
        """,
        (number, title, tags, event, deferral),
    )
