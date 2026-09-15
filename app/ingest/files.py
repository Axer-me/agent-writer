from __future__ import annotations

MAX_FILES = 10
MAX_CHARS_PER_FILE = 80_000


def _decode_bytes(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_txt(data: bytes) -> str:
    return _decode_bytes(data)


def read_docx(data: bytes) -> str:
    from io import BytesIO

    from docx import Document

    doc = Document(BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def read_pdf(data: bytes) -> str:
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text)
    return "\n\n".join(pages)


def extract_upload(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".txt") or name.endswith(".md"):
        text = read_txt(data)
    elif name.endswith(".docx"):
        text = read_docx(data)
    elif name.endswith(".pdf"):
        text = read_pdf(data)
    else:
        raise ValueError(f"Формат не поддержан: {filename}. Нужны txt, md, docx, pdf.")
    text = text.strip()
    if len(text) > MAX_CHARS_PER_FILE:
        text = text[:MAX_CHARS_PER_FILE]
    return text


def long_fragments(text: str, want: int = 10, min_len: int = 800) -> list[str]:
    blocks = [b.strip() for b in text.replace("\r\n", "\n").split("\n\n") if b.strip()]
    long = [b for b in blocks if len(b) >= min_len]
    if len(long) >= want:
        return long[:want]
    # sliding windows of ~2000 chars
    joined = "\n\n".join(blocks) if blocks else text
    if len(joined) < min_len:
        return [joined] if joined else []
    size = 2200
    out = []
    for i in range(0, len(joined), size):
        chunk = joined[i : i + size].strip()
        if len(chunk) >= min_len // 2:
            out.append(chunk)
        if len(out) >= want:
            break
    return out[:want]
