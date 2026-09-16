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
        SQLiteLineageStore().link(c, "p", node_ids=("derived",), thread_ids=(thread,))

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
        review = c.execute("SELECT * FROM curator_review").fetchone()
        assert review["kind"] == "suspect_node" and review["subject"] == "derived"
    Defects(database).invalidate(
        "p", category="fact", reason="Wrong derivation", trace_id="trace"
    )
    with database.connection() as c:
        assert c.execute("SELECT count(*) FROM curator_review").fetchone()[0] == 1


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


def test_approved_telegram_layout_accepts_named_token_variables(tmp_path):
    path = tmp_path / "telegram.yaml"
    path.write_text(
        "owner_id: 123\nsupergroup_id: -100123\nchannel_id: -100124\n"
        "topics: {diary: 1, author: 2, curator: 3, chat: 4, "
        "library: 5, machine: 6, control: 7}\n"
        "bots: {mika: TEST_MIKA_TOKEN, curator: TEST_CURATOR_TOKEN, "
        "ops: TEST_OPS_TOKEN}\n"
    )
    result = TelegramLayout.from_file(path)
    assert result.group_id == -100123 and result.bots["mika"] == "TEST_MIKA_TOKEN"


@pytest.mark.parametrize("channel", [None, 0, "", "   "])
def test_empty_channel_id_publishes_only_to_diary(channel, tmp_path):
    path = tmp_path / "telegram.yaml"
    data = layout().model_dump()
    data["channel_id"] = channel
    from ruamel.yaml import YAML

    YAML().dump(data, path)
    destinations = TelegramLayout.from_file(path).publication_destinations()
    assert len(destinations) == 1
    assert destinations[0].channel == "diary"


async def test_fact_deletion_requires_owner_confirmation_and_expires(
    database, tmp_path
):
    from datetime import timedelta

    from src.core.time_utils import now

    clock = [now()]
    database.run_transaction(
        lambda c: c.executemany(
            "INSERT INTO people_facts(id,person_id,fact) VALUES (?,?,?)",
            [(1, "123", "Owner fact"), (2, "456", "Other person's fact")],
        )
    )
    service = CommandService(
        database,
        layout(),
        SettingsRegistry.from_file(Path("config/settings.yaml")),
        LibraryInbox(tmp_path / "library"),
        log_path=tmp_path / "events.jsonl",
        clock=lambda: clock[0],
    )
    message = SimpleNamespace(
        text="/facts wipe",
        chat=SimpleNamespace(type="private", id=123),
        from_user=SimpleNamespace(id=123),
    )
    await service.command(message, AsyncMock(), "request")
    with database.connection() as c:
        payload = json.loads(c.execute("SELECT payload FROM outbox").fetchone()[0])
        assert c.execute("SELECT count(*) FROM people_facts").fetchone()[0] == 2
    token = payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(
        ":"
    )[-1]
    await service.confirm(token, message, "bad-owner", owner_id=456)
    clock[0] += timedelta(seconds=61)
    await service.confirm(token, message, "expired", owner_id=123)
    with database.connection() as c:
        assert c.execute("SELECT count(*) FROM people_facts").fetchone()[0] == 2
    await service.command(message, AsyncMock(), "request2")
    with database.connection() as c:
        payload = json.loads(
            c.execute("SELECT payload FROM outbox ORDER BY id DESC LIMIT 1").fetchone()[
                0
            ]
        )
    token = payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(
        ":"
    )[-1]
    await service.confirm(token, message, "confirmed", owner_id=123)
    with database.connection() as c:
        assert [row[0] for row in c.execute("SELECT person_id FROM people_facts")] == [
            "456"
        ]


def test_state_command_uses_durable_learner_snapshot(database, tmp_path):
    from src.orchestrator import State
    from src.runner import SQLiteLearningStore

    state = State(3, 0.6, topic="security")
    SQLiteLearningStore(database, state)
    service = CommandService(
        database,
        layout(),
        SettingsRegistry.from_file(Path("config/settings.yaml")),
        LibraryInbox(tmp_path / "library"),
        log_path=tmp_path / "events.jsonl",
    )
    assert service._state()["learning_state"]["topic"] == "security"


@pytest.mark.parametrize(
    "text",
    [
        "/state",
        "/graph",
        "/set",
        "/set study.quiz_threshold",
        "/defects",
        "/outbox review",
    ],
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
