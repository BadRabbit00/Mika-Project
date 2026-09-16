"""Owner commands, registry validation, and Markdown import boundaries."""

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.commands import CommandService
from src.core.db import Database
from src.core.ops_log import OpsMirror
from src.core.settings import (
    MissingSettingsStorage,
    SettingsRegistry,
    SQLiteSettingsStore,
)
from src.core.telegram import TelegramLayout
from src.defects import Defects, SQLiteLineageStore
from src.library import LibraryInbox
from src.publish import Publisher


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "commands.sqlite3")
    database.initialize()
    return database


def test_settings_registry_validates_types_ranges_and_locked_keys():
    registry = SettingsRegistry.from_file(Path("config/settings.yaml"))
    assert registry.validate("study.quiz_threshold", "0.7") == 0.7
    assert registry.validate("mood.enabled", "false") is False
    for key, value in [
        ("study.quiz_threshold", "0.95"),
        ("mood.enabled", "yes"),
        ("curator.effort", "invented"),
        ("system.owner_id", "123"),
        ("rhythm.quiet_from", "26:00"),
    ]:
        with pytest.raises(ValueError):
            registry.validate(key, value)


def test_settings_reads_defaults_without_inventing_missing_storage():
    registry = SettingsRegistry.from_file(Path("config/settings.yaml"))
    assert registry.get("study.quiz_threshold") == 0.6
    with pytest.raises(MissingSettingsStorage):
        registry.set("study.quiz_threshold", "0.7", trace_id="setting")


def test_settings_overrides_survive_reopen_with_explicit_storage_contract(database):
    registry = SettingsRegistry.from_file(
        Path("config/settings.yaml"), store=SQLiteSettingsStore(database)
    )
    registry.set("study.quiz_threshold", "0.7", trace_id="setting-trace")
    reopened = SettingsRegistry.from_file(
        Path("config/settings.yaml"), store=SQLiteSettingsStore(Database(database.path))
    )
    assert reopened.get("study.quiz_threshold") == 0.7
    assert reopened.describe("study.quiz_threshold")["changed"]["at"].endswith("Z")


def test_defect_propagates_only_explicit_post_lineage(database):

    def seed(c):
        c.execute("INSERT INTO posts(id) VALUES ('p')")
        c.execute(
            "INSERT INTO nodes(id, name) "
            "VALUES ('derived', 'Derived'), ('other', 'Other')"
        )
        thread = c.execute(
            "INSERT INTO threads(text, status) VALUES ('Derived thread', 'open')"
        ).lastrowid
        c.execute("INSERT INTO post_nodes VALUES ('p', 'derived')")
        c.execute("INSERT INTO post_threads VALUES ('p', ?)", (thread,))

    database.run_transaction(seed)
    Defects(database, lineage=SQLiteLineageStore()).invalidate(
        "p", category="fact", reason="Wrong derivation", trace_id="trace"
    )
    with database.connection() as c:
        assert (
            c.execute("SELECT suspect FROM nodes WHERE id='derived'").fetchone()[0] == 1
        )
        assert (
            c.execute("SELECT suspect FROM nodes WHERE id='other'").fetchone()[0] == 0
        )
        assert c.execute("SELECT status FROM threads").fetchone()[0] == "stale"


def test_library_import_rejects_missing_origin_and_path_traversal(tmp_path):
    library = LibraryInbox(tmp_path / "library")
    valid = (
        b"---\nid: article\ntitle: An article\ntopic: security\n"
        b"origin_key: source:1\n---\nArticle content.\n"
    )
    with pytest.raises(ValueError, match="filename"):
        library.accept("../../escape.md", valid, trace_id="upload")
    with pytest.raises(ValueError, match="origin_key"):
        library.accept(
            "article.md",
            valid.replace(b"origin_key: source:1\n", b""),
            trace_id="upload",
        )
    first = library.accept("article.md", valid, trace_id="upload")
    second = library.accept("article.md", valid, trace_id="upload-replay")
    assert first.path == second.path and first.id == "article"
    with pytest.raises(ValueError, match="changed"):
        library.accept("article.md", valid + b"Changed", trace_id="changed")
    assert not (tmp_path / "escape.md").exists()


def test_defect_excludes_narrative_without_deleting_the_post(database):
    database.run_transaction(
        lambda c: (
            c.execute(
                "INSERT INTO posts(id, kind, text, state) "
                "VALUES ('p', 'summary', 'Original text', 'published')"
            ),
            c.execute(
                "INSERT INTO narrative(post_id, gist) VALUES ('p', 'Prior gist')"
            ),
        )
    )
    defects = Defects(database)
    defects.invalidate(
        "p", category="fact", reason="Incorrect claim", trace_id="defect"
    )
    with database.connection() as c:
        assert c.execute("SELECT excluded FROM narrative").fetchone()[0] == 1
        assert c.execute("SELECT text FROM posts").fetchone()[0] == "Original text"
        assert c.execute("SELECT trace_id FROM invalidated").fetchone()[0] == "defect"


def layout():
    return TelegramLayout(
        owner_id=123,
        group_id=-100123,
        channel_id=-100124,
        topics=dict(
            diary=1, author=2, curator=3, chat=4, library=5, machine=6, control=7
        ),
    )


@pytest.mark.parametrize(
    "text", ["/state", "/graph", "/set", "/set study.quiz_threshold", "/defects"]
)
async def test_owner_commands_without_arguments_return_state(database, tmp_path, text):
    service = CommandService(
        database,
        layout(),
        SettingsRegistry.from_file(Path("config/settings.yaml")),
        LibraryInbox(tmp_path / "library"),
        log_path=tmp_path / "events.jsonl",
    )
    message = SimpleNamespace(text=text, chat=SimpleNamespace(type="supergroup"))
    await service.command(message, AsyncMock(), "command-trace")
    with database.connection() as connection:
        row = connection.execute("SELECT payload FROM outbox").fetchone()
        payload = json.loads(row[0])
        assert payload["trace_id"] == "command-trace"
        assert payload["destination"]["topic_id"] == 7
        assert "error" not in payload.get("text", "")


@pytest.mark.parametrize("selector", ["", "study", "system"])
async def test_settings_menu_uses_registry_and_hides_locked_keys(
    database, tmp_path, selector
):
    registry = SettingsRegistry.from_file(Path("config/settings.yaml"))
    service = CommandService(
        database,
        layout(),
        registry,
        LibraryInbox(tmp_path / "library"),
        log_path=tmp_path / "events.jsonl",
    )
    message = SimpleNamespace(
        text=f"/set {selector}", chat=SimpleNamespace(type="supergroup")
    )
    await service.command(message, AsyncMock(), "settings-menu")
    with database.connection() as connection:
        payload = json.loads(
            connection.execute("SELECT payload FROM outbox").fetchone()[0]
        )
    buttons = payload["reply_markup"]["inline_keyboard"]
    selectors = {
        button["callback_data"].split(":", 2)[2] for row in buttons for button in row
    }
    expected = (
        set(registry.groups)
        if not selector
        else {row["key"] for row in registry.describe(selector)}
    )
    assert selectors == expected
    assert "system.owner_id" not in selectors
    assert all(
        len(button["callback_data"].encode()) <= 64 for row in buttons for button in row
    )


async def test_machine_log_full_mode_queues_complete_attachment(database):
    registry = SettingsRegistry.from_file(Path("config/settings.yaml"))
    registry.entries["log.verbosity"]["default"] = "full"
    mirror = OpsMirror(Publisher(database), layout().destination("machine"), registry)
    event = {
        "event": "curator_call_completed",
        "trace_id": "chain",
        "user": "U" * 9000,
        "system": "S" * 9000,
        "output": "complete output",
        "cost_usd": 0.012,
    }
    mirror.emit(
        logging.LogRecord("blogai.curator", logging.INFO, "", 0, event, (), None)
    )
    await mirror.drain()
    with database.connection() as connection:
        rows = [
            json.loads(row[0])
            for row in connection.execute("SELECT payload FROM outbox ORDER BY id")
        ]
    assert len(rows) == 2 and len(rows[0]["text"]) < 4096
    assert json.loads(rows[1]["content"])["user"] == "U" * 9000
    assert all(row["destination"]["bot"] == "ops" for row in rows)
