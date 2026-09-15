from __future__ import annotations

from app.ingest.files import MAX_FILES, extract_upload, long_fragments
from app.ingest.web import fragments_from_url
from app.llm.router import complete_with_continue
from app.pipeline.prompts import HUMAN_PROSE, comment_block, pack
from app.storage.project import BookProject


SYSTEM = f"""По длинным фрагментам чужого текста составь шпаргалку СТИЛЯ, не пересказ сюжета.
Только русский. Не копируй чужие абзацы целиком.
Разделы markdown:
# Ритм и длина фраз
# Лексика и тон
# Диалоги
# Описание
# Типичные приёмы
# Чего избегать, чтобы не стать пародией
# Известные факты о манере (если видны из текста или пользователь дописал)
{HUMAN_PROSE}
"""


def generate_style_from_fragments(
    book: BookProject,
    fragments: list[str],
    comment: str | None = None,
    extra_notes: str = "",
) -> str:
    packed_frags = []
    for i, frag in enumerate(fragments[:10], start=1):
        packed_frags.append(f"--- фрагмент {i} ---\n{frag[:4500]}")
    result = complete_with_continue(
        "style",
        [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": pack(
                    extra_notes,
                    "\n\n".join(packed_frags),
                    "Текущий style.md:",
                    book.read_text("style.md") or "(пусто)",
                    comment_block(comment),
                ),
            },
        ],
        max_tokens=2500,
        temperature=0.4,
    )
    if not result.ok:
        raise RuntimeError(result.error or "Не удалось вывести стиль")
    book.write_text("style.md", result.text)
    return result.text


def ingest_files(book: BookProject, files: list[tuple[str, bytes]], comment: str | None = None) -> str:
    if not files:
        raise RuntimeError("Нет файлов")
    if len(files) > MAX_FILES:
        raise RuntimeError(f"Максимум {MAX_FILES} файлов")
    all_text = []
    errors = []
    for name, data in files:
        try:
            all_text.append(extract_upload(name, data))
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    if not all_text:
        raise RuntimeError("Не прочитано ни одного файла. " + "; ".join(errors))
    frags: list[str] = []
    for text in all_text:
        frags.extend(long_fragments(text, want=max(1, 10 // max(len(all_text), 1))))
    frags = frags[:10]
    note = "Файлы пользователя. " + ("; ".join(errors) if errors else "")
    return generate_style_from_fragments(book, frags, comment=comment, extra_notes=note)


def ingest_url(book: BookProject, url: str, cookies: str = "", comment: str | None = None) -> str:
    frags, err = fragments_from_url(url, cookies=cookies)
    if err:
        raise RuntimeError(err)
    return generate_style_from_fragments(
        book, frags, comment=comment, extra_notes=f"Источник URL: {url}"
    )
