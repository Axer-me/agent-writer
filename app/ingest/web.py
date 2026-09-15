from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx

from app.ingest.files import long_fragments


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "article"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = re.sub(r"[ \t]+", " ", data)
        if text.strip():
            self.parts.append(text)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    text = "".join(parser.parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_url(url: str, cookies: str = "", timeout: float = 25.0) -> tuple[str, str | None]:
    """Возвращает (текст, ошибка)."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return "", "Нужен http(s) URL."
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
    }
    cookie_header = cookies.strip()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            request_headers = dict(headers)
            if cookie_header:
                request_headers["Cookie"] = cookie_header
            response = client.get(url, headers=request_headers)
    except httpx.HTTPError as exc:
        return "", f"Сеть: {exc}"
    if response.status_code in {401, 403}:
        return "", f"Сайт ответил {response.status_code}. Вставьте куки или загрузите файлы."
    if response.status_code >= 400:
        return "", f"Сайт ответил HTTP {response.status_code}."
    ctype = response.headers.get("content-type", "")
    raw = response.text
    if "html" in ctype or raw.lstrip().lower().startswith("<!doctype") or "<html" in raw[:200].lower():
        text = html_to_text(raw)
    else:
        text = raw
    if len(text.strip()) < 400:
        return "", "Слишком мало текста со страницы. Загрузите файлы (txt/docx/pdf)."
    return text, None


def fragments_from_url(url: str, cookies: str = "") -> tuple[list[str], str | None]:
    text, err = fetch_url(url, cookies=cookies)
    if err:
        return [], err
    frags = long_fragments(text, want=10)
    if not frags:
        return [], "Не удалось нарезать длинные фрагменты. Загрузите файлы."
    return frags, None
