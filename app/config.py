from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    load_dotenv(ROOT / ".env", override=True)


_load_env()

PROJECTS_DIR = ROOT / "projects"
DEFAULT_OR_MODEL = "z-ai/glm-5.2:free"
DEFAULT_GC_MODEL = "GigaChat-2-Max"
APP_PORT = 8765
APP_HOST = "127.0.0.1"


def _first_env(*names: str, default: str = "") -> str:
    _load_env()
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def openrouter_api_key() -> str:
    return _first_env("OPEN_ROUTER_API_KEY", "OPENROUTER_API_KEY")


def openrouter_model() -> str:
    return _first_env("OPENROUTER_MODEL", default=DEFAULT_OR_MODEL)


def gigachat_credentials() -> str:
    return _first_env("GIGACHAT_CREDENTIALS", "GIGACHAT_API_KEY")


def gigachat_scope() -> str:
    return _first_env("GIGACHAT_SCOPE", default="GIGACHAT_API_PERS")


def gigachat_model() -> str:
    return _first_env("GIGACHAT_MODEL", default=DEFAULT_GC_MODEL)


def gigachat_verify_ssl() -> bool:
    _load_env()
    raw = os.getenv("GIGACHAT_VERIFY_SSL", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def ensure_dirs() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
