from app.storage.project import create_project, delete_project, get_project, list_projects


def test_create_list_delete(tmp_path, monkeypatch):
    from app import config
    from app.storage import project as project_mod

    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(project_mod, "PROJECTS_DIR", tmp_path)
    book = create_project("Тестовая книга")
    assert (book.root / "state.json").exists()
    assert (book.root / "card.md").exists()
    assert book.read_state()["title"] == "Тестовая книга"
    ids = {p["id"] for p in list_projects()}
    assert book.id in ids
    again = get_project(book.id)
    again.write_text("card.md", "# hi")
    assert get_project(book.id).read_text("card.md") == "# hi"
    delete_project(book.id)
    assert book.id not in {p["id"] for p in list_projects()}
