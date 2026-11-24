from __future__ import annotations

import json
import queue
import threading
import traceback
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import APP_HOST, APP_PORT, ensure_dirs, gigachat_model, openrouter_model
from app.llm.gigachat import ping_gigachat
from app.llm.openrouter import list_free_text_models, ping_openrouter
from app.llm.router import configured_openrouter_ok
from app.pipeline.bible import generate_bible
from app.pipeline.card import generate_card, sync_card_flags
from app.pipeline.chapter import accept_chapter, generate_chapter, revise_fragment
from app.pipeline.continuity import missing_character_hints
from app.pipeline.approvals import stale_after
from app.pipeline.drama import generate_drama, parse_drama
from app.pipeline.plan import book_plan_block_reason, chapter_start_block, generate_plan
from app.pipeline.scale import CHAPTER_CAP, normalize_size
from app.pipeline.style import ingest_files, ingest_url
from app.storage.embeddings import selftest as embeddings_selftest
from app.storage.project import (
    ARTIFACTS,
    create_project,
    delete_project,
    get_project,
    list_projects,
)

WEB = Path(__file__).resolve().parent / "web"
ensure_dirs()
_CHAPTER_CANCEL: dict[str, threading.Event] = {}
_CHAPTER_RUNNING: set[str] = set()
_CHAPTER_LOCK = threading.Lock()

app = FastAPI(title="Агент-писатель", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=WEB), name="static")


class TitleBody(BaseModel):
    title: str = "Новая книга"
    book_size: str = "medium"


class SizeBody(BaseModel):
    book_size: str = "medium"


class GenerateBody(BaseModel):
    comment: str | None = None


class SaveBody(BaseModel):
    content: str = ""


class UrlBody(BaseModel):
    url: str
    cookies: str = ""
    comment: str | None = None


class ReviseBody(BaseModel):
    selection: str
    comment: str = ""


class CourseBody(BaseModel):
    note: str = ""


class PlanChapterBody(BaseModel):
    number: int
    title: str = ""
    tags: str = ""
    event: str = ""
    deferral: str = ""
    reveal: str = ""


class PlanSaveBody(BaseModel):
    chapters: list[PlanChapterBody] = []
    drama: dict[str, str] | None = None
    spine: dict[str, str] | None = None
    world: dict[str, str] | None = None


def _mark_stale(book, source: str) -> None:
    state = book.read_state()
    stale = stale_after(
        state.get("stale") or {},
        source,
        is_original=bool(state.get("is_original", True)),
    )
    book.patch_state(stale=stale)


def _same_text(previous: str, incoming: str) -> bool:
    """Повторная запись того же текста — не правка."""
    return (previous or "").replace("\r\n", "\n") == (incoming or "").replace("\r\n", "\n")


def _plan_same(stored: list, incoming: list) -> bool:
    def sign(row) -> tuple:
        data = row if isinstance(row, dict) else row.model_dump()
        return (
            int(data.get("number") or 0),
            data.get("title") or "",
            data.get("tags") or "",
            data.get("event") or "",
            data.get("reveal") or "",
        )

    return [sign(row) for row in stored] == [sign(row) for row in incoming]


def _sheet_same(stored: dict | None, incoming: dict | None, keys: tuple[str, ...]) -> bool:
    if incoming is None:
        return True
    current = stored or {}
    return all((current.get(key) or "") == (incoming.get(key) or "") for key in keys)


def _book(project_id: str):
    try:
        return get_project(project_id)
    except FileNotFoundError:
        raise HTTPException(404, "Книга не найдена") from None


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


_HEALTH_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}


@app.get("/api/health/llm")
def health_llm():
    import time

    cached = _HEALTH_CACHE.get("payload")
    if cached is not None and time.monotonic() - float(_HEALTH_CACHE["at"]) < 600:
        return cached
    or_err = None
    try:
        configured_openrouter_ok()
    except Exception as exc:
        or_err = str(exc)
    or_ping = ping_openrouter()
    gc_ping = ping_gigachat()
    or_limited = bool(or_ping.error and "429" in (or_ping.error or ""))
    payload = {
        "openrouter": {
            "ok": or_ping.ok or or_limited,
            "rate_limited": or_limited,
            "model": or_ping.model,
            "error": None if or_ping.ok else or_ping.error,
            "sample": (or_ping.text or "")[:80],
        },
        "gigachat": {
            "ok": gc_ping.ok,
            "model": gc_ping.model,
            "error": gc_ping.error,
            "censored": gc_ping.censored,
            "sample": (gc_ping.text or gc_ping.extra.get("raw_text") or "")[:80],
        },
    }
    _HEALTH_CACHE["at"] = time.monotonic()
    _HEALTH_CACHE["payload"] = payload
    return payload


@app.get("/api/models/openrouter")
def or_models():
    try:
        models = list_free_text_models()
    except Exception as exc:
        raise HTTPException(502, f"Не удалось получить список моделей: {exc}") from exc
    return {"default": openrouter_model(), "gigachat_model": gigachat_model(), "models": models}


@app.get("/api/embeddings/selftest")
def emb_selftest():
    try:
        return embeddings_selftest()
    except Exception as exc:
        raise HTTPException(500, f"Эмбеддинги: {exc}") from exc


@app.get("/api/projects")
def api_list_projects():
    return {"projects": list_projects()}


@app.post("/api/projects")
def api_create(body: TitleBody):
    book = create_project(body.title, body.book_size)
    return book.public_dict()


@app.put("/api/projects/{project_id}/size")
def api_size(project_id: str, body: SizeBody):
    book = _book(project_id)
    state = book.read_state()
    stale = dict(state.get("stale") or {})
    stale["drama"] = True
    stale["plan"] = True
    book.patch_state(book_size=normalize_size(body.book_size), stale=stale)
    return book.public_dict()


@app.get("/api/projects/{project_id}")
def api_get(project_id: str):
    return _book(project_id).public_dict()


@app.get("/api/projects/{project_id}/chapters/{number}")
def api_read_chapter(project_id: str, number: int, draft: bool = False):
    if not 1 <= number <= CHAPTER_CAP:
        raise HTTPException(404, "Нет такой главы")
    book = _book(project_id)
    row = next((item for item in book.chapter_catalog() if item["number"] == number), None)
    text = book.read_chapter(number, draft=draft)
    return {"text": text, "chapter": row}


@app.delete("/api/projects/{project_id}")
def api_delete(project_id: str):
    delete_project(project_id)
    return {"ok": True}


@app.put("/api/projects/{project_id}/files/{name}")
def api_save_file(project_id: str, name: str, body: SaveBody):
    if name not in ARTIFACTS:
        raise HTTPException(400, "Неизвестный файл")
    book = _book(project_id)
    if _same_text(book.read_text(name), body.content):
        return {"ok": True, "project": book.public_dict()}
    book.write_text(name, body.content)
    if name == "card.md":
        sync_card_flags(book, body.content)
        _mark_stale(book, "card")
    elif name == "bible.md":
        _mark_stale(book, "bible")
    elif name == "drama.md":
        from app.pipeline.plan import _store

        _store(book).save_drama(parse_drama(body.content))
        _mark_stale(book, "drama")
    elif name == "plan.md":
        from app.pipeline.plan import _store, rows_from_plan_text

        _store(book).upsert_plan(rows_from_plan_text(body.content))
        _mark_stale(book, "plan")
    return {"ok": True, "project": book.public_dict()}


@app.post("/api/projects/{project_id}/generate/{stage}")
def api_generate(project_id: str, stage: str, body: GenerateBody):
    book = _book(project_id)
    generators = {
        "card": generate_card,
        "bible": generate_bible,
        "drama": generate_drama,
        "plan": generate_plan,
    }
    fn = generators.get(stage)
    if not fn:
        raise HTTPException(400, "Этап генерируется иначе (стиль/глава)")
    try:
        text = fn(book, body.comment)
        if stage == "card":
            _mark_stale(book, "card")
        elif stage == "bible":
            _mark_stale(book, "bible")
        elif stage == "drama":
            _mark_stale(book, "drama")
        elif stage == "plan":
            _mark_stale(book, "plan")
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"text": text, "project": book.public_dict()}


@app.put("/api/projects/{project_id}/plan")
def api_save_plan(project_id: str, body: PlanSaveBody):
    from app.pipeline.plan import _store

    book = _book(project_id)
    rows = [item.model_dump() for item in body.chapters]
    store = _store(book)
    if body.drama is not None and not _sheet_same(
        store.drama(),
        body.drama,
        ("hero", "want", "belief", "cost", "becomes", "act1", "act2", "act3", "world1", "world2", "world3"),
    ):
        from app.pipeline.drama import render_drama

        store.save_drama(body.drama)
        book.write_text("drama.md", render_drama(body.drama) + "\n")
        _mark_stale(book, "drama")
    if body.spine is not None and not _sheet_same(
        store.spine(),
        body.spine,
        ("finale", "act1", "act2", "act3"),
    ):
        store.save_spine(body.spine)
    if body.world is not None and not _sheet_same(
        store.world(),
        body.world,
        ("when_where", "daily", "first_hour", "hidden"),
    ):
        store.save_world(body.world)
    if rows and not _plan_same(store.plan_chapters(), rows):
        store.upsert_plan(rows)
        book.patch_state(chapter_count=max(int(row["number"]) for row in rows))
        _mark_stale(book, "plan")
        from app.pipeline.continuity import ensure_fresh_arc

        ensure_fresh_arc(book, force=True)
    return book.public_dict()


@app.post("/api/projects/{project_id}/style/files")
async def api_style_files(
    project_id: str,
    comment: str = Form(""),
    files: list[UploadFile] = File(...),
):
    book = _book(project_id)
    payload: list[tuple[str, bytes]] = []
    for item in files[:10]:
        payload.append((item.filename or "file.txt", await item.read()))
    try:
        text = ingest_files(book, payload, comment=comment or None)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"text": text, "project": book.public_dict()}


@app.post("/api/projects/{project_id}/style/url")
def api_style_url(project_id: str, body: UrlBody):
    book = _book(project_id)
    try:
        text = ingest_url(book, body.url, cookies=body.cookies, comment=body.comment)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"text": text, "project": book.public_dict()}


@app.post("/api/projects/{project_id}/style/generate")
def api_style_comment(project_id: str, body: GenerateBody):
    """Переписать style.md по комментарию, без новых фрагментов."""
    from app.pipeline.style import generate_style_from_fragments

    book = _book(project_id)
    current = book.read_text("style.md")
    if not current.strip() and not (body.comment or "").strip():
        raise HTTPException(400, "Нет стиля: загрузите файлы или URL, либо напишите комментарий с фактами об авторе.")
    try:
        text = generate_style_from_fragments(
            book,
            [current or "нет фрагментов"],
            comment=body.comment,
            extra_notes="Перепись шпаргалки стиля.",
        )
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"text": text, "project": book.public_dict()}


@app.get("/api/projects/{project_id}/hints")
def api_hints(project_id: str):
    return {"hints": missing_character_hints(_book(project_id))}


@app.post("/api/projects/{project_id}/chapter/generate")
def api_chapter_generate(project_id: str):
    book = _book(project_id)
    reason = chapter_start_block(book)
    if reason:
        raise HTTPException(400, reason)
    with _CHAPTER_LOCK:
        if project_id in _CHAPTER_RUNNING:
            raise HTTPException(409, "Глава уже пишется. Дождитесь конца или нажмите «Остановить».")
        _CHAPTER_RUNNING.add(project_id)
    q: queue.Queue = queue.Queue()
    cancel = threading.Event()
    _CHAPTER_CANCEL[project_id] = cancel

    def on_progress(step: str, data: dict[str, Any]) -> None:
        q.put(("progress", {"step": step, **data}))

    def worker() -> None:
        from app.pipeline.jobs import Cancelled, bind_job, reset_job

        tokens = bind_job(book, cancel)
        try:
            result = generate_chapter(book, on_progress=on_progress)
            text = result.get("text") or ""
            number = int(result.get("chapter") or book.read_state().get("chapter_number") or 1)
            if text.strip():
                book.write_chapter(number, text, draft=True)
            q.put(("done", result))
        except Cancelled as exc:
            q.put(("error", {"error": str(exc)}))
        except Exception as exc:
            message = str(exc)
            from app.pipeline.chapter import read_scene_texts

            number = int(book.read_state().get("chapter_number") or 1)
            if read_scene_texts(book, number):
                message += " Уже записанные куски сохранятся, следующий запуск продолжит их."
            q.put(
                (
                    "error",
                    {"error": message, "trace": traceback.format_exc()[-800]},
                )
            )
        finally:
            reset_job(tokens)
            _CHAPTER_CANCEL.pop(project_id, None)
            _CHAPTER_RUNNING.discard(project_id)

    threading.Thread(target=worker, daemon=True).start()

    def events():
        while True:
            kind, payload = q.get()
            yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if kind in {"done", "error"}:
                break

    return StreamingResponse(events(), media_type="text/event-stream")


class CanonBody(BaseModel):
    content: str = ""


@app.put("/api/projects/{project_id}/canon")
def api_canon(project_id: str, body: CanonBody):
    from app.pipeline.plan import _store

    book = _book(project_id)
    _store(book).replace_canon_notes(body.content)
    return book.public_dict()


@app.post("/api/projects/{project_id}/chapter/cancel")
def api_chapter_cancel(project_id: str):
    _book(project_id)
    event = _CHAPTER_CANCEL.get(project_id)
    if event:
        event.set()
    return {"ok": True}


@app.post("/api/projects/{project_id}/chapter/revise")
def api_revise(project_id: str, body: ReviseBody):
    book = _book(project_id)
    try:
        text = revise_fragment(book, body.selection, body.comment)
    except Exception as extra:
        raise HTTPException(502, str(extra)) from extra
    from app.pipeline.overlap import check_draft_overlap

    number = int(book.read_state().get("chapter_number") or 1)
    overlaps = check_draft_overlap(book, text, number)
    return {"text": text, "overlaps": overlaps, "word_count": len(text.split())}


@app.post("/api/projects/{project_id}/chapter/accept")
def api_accept(project_id: str):
    book = _book(project_id)
    try:
        result = accept_chapter(book)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@app.post("/api/projects/{project_id}/chapter/course")
def api_course(project_id: str, body: CourseBody):
    book = _book(project_id)
    book.patch_state(course_note=body.note.strip())
    return book.public_dict()


@app.post("/api/projects/{project_id}/chapter/save-draft")
def api_save_draft(project_id: str, body: SaveBody):
    book = _book(project_id)
    number = int(book.read_state().get("chapter_number") or 1)
    book.write_chapter(number, body.content, draft=True)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=True)
