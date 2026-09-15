from app.storage.project import create_project, delete_project, get_project, list_projects
from app.config import PROJECTS_DIR
from pathlib import Path
import tempfile
import os

# isolated dir
import app.config as config
import app.storage.project as project_mod

tmp = Path(tempfile.mkdtemp())
config.PROJECTS_DIR = tmp
project_mod.PROJECTS_DIR = tmp
book = create_project("Тестовая книга")
assert (book.root / "state.json").exists()
assert book.read_state()["title"] == "Тестовая книга"
assert book.id in {p["id"] for p in list_projects()}
get_project(book.id).write_text("card.md", "# hi")
assert get_project(book.id).read_text("card.md") == "# hi"
delete_project(book.id)
assert book.id not in {p["id"] for p in list_projects()}
print("store_ok", tmp)
