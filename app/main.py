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
from app.pipeline.card import generate_card
from app.pipeline.chapter import accept_chapter, generate_chapter, revise_fragment
from app.pipeline.continuity import missing_character_hints
from app.pipeline.plan import generate_plan
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

app = FastAPI(title="Агент-писатель", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=WEB), name="static")


class TitleBody(BaseModel):
    title: str = "Новая книга"


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


class ApproveBody(BaseModel):
    stage: str


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


@app.get("/api/health/llm")
def health_llm():
    or_err = None
    try:
        configured_openrouter_ok()
    except Exception as exc:
        or_err = str(exc)
    or_ping = ping_openrouter()
    gc_ping = ping_gigachat()
    or_limited = bool(or_ping.error and "429" in (or_ping.error or ""))
    return {
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
    book = create_project(body.title)
    return book.public_dict()


@app.get("/api/projects/{project_id}")
def api_get(project_id: str):
    return _book(project_id).public_dict()


@app.delete("/api/projects/{project_id}")
def api_delete(project_id: str):
    delete_project(project_id)
    return {"ok": True}


@app.put("/api/projects/{project_id}/files/{name}")
def api_save_file(project_id: str, name: str, body: SaveBody):
    if name not in ARTIFACTS:
        raise HTTPException(400, "Неизвестный файл")
    book = _book(project_id)
    book.write_text(name, body.content)
    return {"ok": True}


@app.post("/api/projects/{project_id}/generate/{stage}")
def api_generate(project_id: str, stage: str, body: GenerateBody):
    book = _book(project_id)
    generators = {
        "card": generate_card,
        "bible": generate_bible,
        "plan": generate_plan,
    }
    fn = generators.get(stage)
    if not fn:
        raise HTTPException(400, "Этап генерируется иначе (стиль/глава)")
    try:
        text = fn(book, body.comment)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"text": text, "project": book.public_dict()}


@app.post("/api/projects/{project_id}/approve/{stage}")
def api_approve(project_id: str, stage: str):
    book = _book(project_id)
    state = book.read_state()
    approved = dict(state.get("approved") or {})
    if stage not in approved and stage != "style":
        if stage not in {"card", "bible", "plan", "style"}:
            raise HTTPException(400, "Неизвестный этап")
    approved[stage] = True
    order = ["card", "style", "bible", "plan", "chapter"]
    nxt = state.get("stage") or "card"
    if stage in order:
        idx = order.index(stage)
        nxt = order[min(idx + 1, len(order) - 1)]
    book.patch_state(approved=approved, stage=nxt)
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
    q: queue.Queue = queue.Queue()

    def on_progress(step: str, data: dict[str, Any]) -> None:
        q.put(("progress", {"step": step, **data}))

    def worker() -> None:
        try:
            result = generate_chapter(book, on_progress=on_progress)
            text = result.get("text") or ""
            number = int(result.get("chapter") or book.read_state().get("chapter_number") or 1)
            if text.strip():
                book.write_chapter(number, text, draft=True)
            q.put(("done", result))
        except Exception as exc:
            q.put(
                (
                    "error",
                    {"error": str(exc), "trace": traceback.format_exc()[-800]},
                )
            )

    threading.Thread(target=worker, daemon=True).start()

    def events():
        while True:
            kind, payload = q.get()
            yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if kind in {"done", "error"}:
                break

    return StreamingResponse(events(), media_type="text/event-stream")


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
