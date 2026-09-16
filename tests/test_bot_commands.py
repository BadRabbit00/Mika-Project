"""Owner commands, registry validation, and Markdown import boundaries."""

from pathlib import Path

import pytest

from src.core.db import Database
from src.core.settings import SettingsRegistry, MissingSettingsStorage
from src.library import LibraryInbox
from src.defects import Defects


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "commands.sqlite3")
    database.initialize()
    return database


def test_settings_registry_validates_types_ranges_and_locked_keys():
    registry = SettingsRegistry.from_file(Path("config/settings.yaml"))
    assert registry.validate("study.quiz_threshold", "0.7") == 0.7
    assert registry.validate("mood.enabled", "false") is False
    for key, value in [("study.quiz_threshold", "0.95"), ("mood.enabled", "yes"),
                       ("curator.effort", "invented"), ("system.owner_id", "123"),
                       ("rhythm.quiet_from", "26:00")]:
        with pytest.raises(ValueError):
            registry.validate(key, value)


def test_settings_reads_defaults_without_inventing_missing_storage():
    registry = SettingsRegistry.from_file(Path("config/settings.yaml"))
    assert registry.get("study.quiz_threshold") == 0.6
    with pytest.raises(MissingSettingsStorage):
        registry.set("study.quiz_threshold", "0.7", trace_id="setting")


def test_library_import_rejects_missing_origin_and_path_traversal(tmp_path):
    library = LibraryInbox(tmp_path / "library")
    valid = b"---\nid: article\ntitle: An article\ntopic: security\norigin_key: source:1\n---\nArticle content.\n"
    with pytest.raises(ValueError, match="filename"):
        library.accept("../../escape.md", valid, trace_id="upload")
    with pytest.raises(ValueError, match="origin_key"):
        library.accept("article.md", valid.replace(b"origin_key: source:1\n", b""), trace_id="upload")
    first = library.accept("article.md", valid, trace_id="upload")
    second = library.accept("article.md", valid, trace_id="upload-replay")
    assert first.path == second.path and first.id == "article"
    with pytest.raises(ValueError, match="changed"):
        library.accept("article.md", valid + b"Changed", trace_id="changed")
    assert not (tmp_path / "escape.md").exists()


def test_defect_excludes_narrative_without_deleting_the_post(database):
    database.run_transaction(lambda c: (
        c.execute("INSERT INTO posts(id, kind, text, state) VALUES ('p', 'summary', 'Original text', 'published')"),
        c.execute("INSERT INTO narrative(post_id, gist) VALUES ('p', 'Prior gist')"),
    ))
    defects = Defects(database)
    defects.invalidate("p", category="fact", reason="Incorrect claim", trace_id="defect")
    with database.connection() as c:
        assert c.execute("SELECT excluded FROM narrative").fetchone()[0] == 1
        assert c.execute("SELECT text FROM posts").fetchone()[0] == "Original text"
        assert c.execute("SELECT trace_id FROM invalidated").fetchone()[0] == "defect"
