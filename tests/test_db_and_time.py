"""Step 1 contracts, written before the storage implementation."""

import ast
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

import pytest

from src.core import db, time_utils

ALMATY = ZoneInfo("Asia/Almaty")


@pytest.fixture
def database(tmp_path):
    instance = db.Database(tmp_path / "test.sqlite3")
    instance.initialize()
    return instance


def test_timezone_everywhere():
    instant = datetime(2026, 9, 16, 3, 4, 5, 123456, tzinfo=ALMATY)
    serialized = time_utils.to_utc_iso(instant)
    results = [
        time_utils.now(),
        time_utils.require_aware(instant),
        time_utils.require_aware(instant.astimezone(UTC)),
        time_utils.from_utc_iso(serialized),
        time_utils.from_utc_iso("2026-09-15T22:04:05.123456+00:00"),
    ]
    for value in results:
        assert value.utcoffset() is not None
        assert isinstance(value.tzinfo, ZoneInfo)
        assert value.tzinfo.key == "Asia/Almaty"
    assert serialized == "2026-09-15T22:04:05.123456Z"
    assert time_utils.from_utc_iso(serialized) == instant


class UndefinedOffset(tzinfo):
    def utcoffset(self, value):
        return None


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 9, 16, tzinfo=ALMATY).replace(tzinfo=None),
        datetime(2026, 9, 16, tzinfo=UndefinedOffset()),
    ],
)
def test_naive_datetimes_are_rejected(value, database):
    for convert in (time_utils.require_aware, time_utils.to_utc_iso):
        with pytest.raises(ValueError, match="aware"):
            convert(value)
    with database.connection() as connection:
        with pytest.raises(ValueError, match="aware"):
            connection.execute(
                "INSERT INTO posts(id, published_at) VALUES ('p', ?)", (value,)
            )


@pytest.mark.parametrize(
    "text",
    [
        "2026-09-16",
        "2026-09-16T00:00:00",
        "2026-09-16T00:00:00+05:00",
        "2026-09-16T00:00:00-00:00",
        "2026-02-30T00:00:00Z",
        "2026-09-16 00:00:00Z",
        "20260916T000000Z",
        "not a timestamp",
        "2026-09-16T00:00:00.1234567Z",
        "2026-09-16T00:00:00Z\n",
    ],
)
def test_invalid_database_timestamps_are_rejected(text, database):
    with pytest.raises(ValueError):
        time_utils.from_utc_iso(text)
    with database.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO posts(id, published_at) VALUES ('p', ?)", (text,)
            )


@pytest.mark.parametrize(
    "instant",
    [
        datetime(2026, 9, 16, 0, 0, 0, 123456, tzinfo=ALMATY),
        datetime(2024, 2, 29, 23, 30, tzinfo=ALMATY, fold=0),
        datetime(2024, 2, 29, 23, 30, tzinfo=ALMATY, fold=1),
        datetime(2024, 3, 1, 0, 30, tzinfo=ALMATY),
    ],
)
def test_sqlite_datetime_round_trip_without_shift(database, instant):
    database.run_transaction(
        lambda connection: connection.execute(
            "INSERT INTO posts(id, published_at) VALUES ('p', ?)", (instant,)
        )
    )
    with database.connection() as connection:
        stored = connection.execute("SELECT published_at FROM posts").fetchone()[0]
    assert isinstance(stored, str) and stored.endswith("Z")
    restored = time_utils.from_utc_iso(stored)
    assert restored.astimezone(UTC) == instant.astimezone(UTC)
    assert restored.utcoffset() == instant.utcoffset()
    assert restored.fold == instant.fold
    assert restored.microsecond == instant.microsecond


def test_schema_contains_every_documented_table_and_column(database):
    architecture = Path("ARCHITECTURE.md").read_text()
    tables = re.findall(r"CREATE TABLE (\w+)\s*\((.*?)\);", architecture, re.S)
    assert len(tables) == 24
    with database.connection() as connection:
        for name, definition in tables:
            definition = re.sub(r"--[^\n]*", "", definition)
            columns = re.findall(
                r"(?:^|,)\s*(\w+)\s+(?:TEXT|INTEGER|INT|TIMESTAMP|DATE|REAL|BOOLEAN)\b",
                definition,
            )
            actual = {
                row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')
            }
            assert set(columns) <= actual, name
        for table, columns in {
            "sources": {"complexity", "complexity_why", "new_terms"},
            "nodes": {"suspect"},
            "narrative": {"excluded"},
            "node_embeddings": {"node_id", "embedding"},
        }.items():
            actual = {
                row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            assert columns <= actual
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_wal_and_foreign_keys_on_every_connection(database):
    for _ in range(2):
        with database.connection() as connection:
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                connection.execute(
                    "INSERT INTO edges(src, rel, dst) VALUES ('absent', 'is_a', 'absent')"
                )


def test_only_closed_relation_vocabulary_is_accepted(database):
    allowed = {
        "is_a",
        "defends_against",
        "enables",
        "requires",
        "contradicts",
        "example_of",
        "part_of",
    }
    with database.connection() as connection:
        connection.execute("INSERT INTO nodes(id, name) VALUES ('a', 'A'), ('b', 'B')")
        for relation in allowed:
            connection.execute(
                "INSERT INTO edges(src, rel, dst) VALUES ('a', ?, 'b')", (relation,)
            )
        for relation in ("related_to", "", None):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO edges(src, rel, dst) VALUES ('a', ?, 'b')", (relation,)
                )


def test_fts_tracks_insert_update_delete_and_rollback(database):
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO nodes(id, name, summary) VALUES ('a', 'Sandbox', 'Isolation')"
        )
        assert (
            connection.execute(
                "SELECT id FROM nodes_fts WHERE nodes_fts MATCH 'Isolation'"
            ).fetchone()[0]
            == "a"
        )
        connection.execute("UPDATE nodes SET summary = 'Namespaces' WHERE id = 'a'")
        assert (
            connection.execute(
                "SELECT id FROM nodes_fts WHERE nodes_fts MATCH 'Isolation'"
            ).fetchall()
            == []
        )
        with pytest.raises(RuntimeError), database.transaction(connection):
            connection.execute("DELETE FROM nodes WHERE id = 'a'")
            raise RuntimeError("Roll back this deletion")
        assert (
            connection.execute(
                "SELECT id FROM nodes_fts WHERE nodes_fts MATCH 'Namespaces'"
            ).fetchone()[0]
            == "a"
        )
        connection.execute("DELETE FROM nodes WHERE id = 'a'")
        assert connection.execute("SELECT * FROM nodes_fts").fetchall() == []


def test_migrations_upgrade_and_reopen_without_losing_data(tmp_path):
    instance = db.Database(tmp_path / "upgrade.sqlite3")
    instance.migrate(target_version=1)
    with instance.connection() as connection:
        connection.execute("INSERT INTO nodes(id, name) VALUES ('a', 'Sandbox')")
        assert "suspect" not in {
            row[1] for row in connection.execute("PRAGMA table_info(nodes)")
        }
    assert instance.initialize() == db.SCHEMA_VERSION
    assert db.Database(instance.path).initialize() == db.SCHEMA_VERSION
    with instance.connection() as connection:
        assert connection.execute("SELECT name, suspect FROM nodes").fetchone()[:] == (
            "Sandbox",
            0,
        )
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        )
        assert (
            connection.execute(
                "SELECT id FROM nodes_fts WHERE nodes_fts MATCH 'Sandbox'"
            ).fetchone()[0]
            == "a"
        )


def test_migration_failure_is_atomic(tmp_path, monkeypatch):
    instance = db.Database(tmp_path / "failed.sqlite3")
    monkeypatch.setattr(
        db, "MIGRATIONS", (("CREATE TABLE partial(id INT)", "INVALID SQL"),)
    )
    with pytest.raises(sqlite3.OperationalError):
        instance.migrate(target_version=1)
    with instance.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'partial'"
            ).fetchall()
            == []
        )


def test_future_schema_and_downgrades_are_rejected(database):
    with pytest.raises(ValueError, match="downgrade"):
        database.migrate(target_version=1)
    with database.connection() as connection:
        connection.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    with pytest.raises(ValueError, match="newer"):
        database.initialize()


def test_unversioned_existing_schema_is_not_silently_adopted(tmp_path):
    instance = db.Database(tmp_path / "unversioned.sqlite3")
    with instance.connection() as connection:
        connection.execute("CREATE TABLE posts(id TEXT)")
    with pytest.raises(ValueError, match="unversioned"):
        instance.initialize()


def enqueue(connection, post_id="p", channel="diary", payload=None, **kwargs):
    return db.enqueue_outbox(
        connection,
        post_id=post_id,
        channel=channel,
        payload={"text": "Body"} if payload is None else payload,
        **kwargs,
    )


def test_outbox_idempotent(database):
    at = time_utils.now()
    first = database.run_transaction(
        lambda connection: enqueue(connection, next_try_at=at)
    )
    again = db.Database(database.path).run_transaction(enqueue)
    assert again == first
    with database.connection() as connection:
        row = connection.execute("SELECT * FROM outbox").fetchone()
        assert row["id"] == first and row["attempts"] == 0
        assert json.loads(row["payload"]) == {"text": "Body"}
        assert row["next_try_at"] == time_utils.to_utc_iso(at)
        assert row["sent_at"] is None
        assert connection.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO outbox(idem_key) VALUES (?)", (row["idem_key"],)
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO outbox(idem_key) VALUES (NULL)")


def test_outbox_keys_do_not_collide(database):
    ids = []
    for post, channel in [("a:b", "c"), ("a", "b:c"), ("a", "c")]:
        ids.append(
            database.run_transaction(
                lambda connection, p=post, c=channel: enqueue(connection, p, c)
            )
        )
    assert len(set(ids)) == 3


def test_outbox_conflicting_payload_is_rejected(database):
    database.run_transaction(enqueue)
    with pytest.raises(ValueError, match="payload"):
        database.run_transaction(
            lambda connection: enqueue(connection, payload={"text": "Different"})
        )


def test_outbox_requires_an_atomic_transaction(database):
    with database.connection() as connection:
        with pytest.raises(RuntimeError, match="transaction"):
            enqueue(connection)


def test_post_and_outbox_are_rolled_back_together(database):
    def publish_intent(connection):
        connection.execute("INSERT INTO posts(id, text) VALUES ('p', 'Body')")
        enqueue(connection)
        raise RuntimeError("Failure before commit")

    with pytest.raises(RuntimeError):
        database.run_transaction(publish_intent)
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM posts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_transaction_retries_a_real_sqlite_lock(database, monkeypatch):
    delays = []
    with database.connection() as blocker:
        blocker.execute("BEGIN IMMEDIATE")

        def unlock(delay):
            delays.append(delay)
            blocker.execute("ROLLBACK")

        monkeypatch.setattr(db.time, "sleep", unlock)
        database.run_transaction(
            lambda connection: connection.execute(
                "INSERT INTO topics(name) VALUES ('security')"
            )
        )
    assert delays == [0.2]


def test_lock_retries_are_bounded(database, monkeypatch):
    delays = []
    monkeypatch.setattr(db.time, "sleep", delays.append)
    with database.connection() as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            database.run_transaction(enqueue)
        blocker.execute("ROLLBACK")
    assert delays == [0.2] * 5


def test_non_lock_error_is_not_retried(database, monkeypatch):
    delays = []
    monkeypatch.setattr(db.time, "sleep", delays.append)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        database.run_transaction(
            lambda connection: connection.execute("INSERT INTO absent VALUES (1)")
        )
    assert delays == []


def test_callback_replays_only_after_rollback(database, monkeypatch):
    attempts = []
    monkeypatch.setattr(db.time, "sleep", lambda delay: None)

    def write(connection):
        connection.execute("INSERT INTO topics(name) VALUES ('one')")
        attempts.append(1)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        return 42

    assert database.run_transaction(write) == 42
    assert len(attempts) == 2
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM topics").fetchone()[0] == 1


def test_concurrent_outbox_writers_deduplicate(database):
    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(lambda _: database.run_transaction(enqueue), range(8)))
    assert len(set(ids)) == 1


def test_concurrent_initialization(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    start = Event()

    def initialize(_):
        assert start.wait(timeout=5)
        return db.Database(path).initialize()

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(initialize, i) for i in range(3)]
        start.set()
        assert [future.result(timeout=10) for future in futures] == [
            db.SCHEMA_VERSION
        ] * 3


def test_mood_storage_rounds_pad(database):
    at = time_utils.now()
    database.run_transaction(
        lambda connection: db.insert_mood(
            connection,
            at=at,
            p=0.123456,
            a=-0.234567,
            d=0.345678,
            baseline_p=0.111111,
            baseline_a=0.222222,
            baseline_d=-0.333333,
        )
    )
    with database.connection() as connection:
        assert connection.execute(
            "SELECT p, a, d, baseline_p, baseline_a, baseline_d FROM mood"
        ).fetchone()[:] == (0.1235, -0.2346, 0.3457, 0.1111, 0.2222, -0.3333)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO mood(at, p) VALUES (?, 0.123456)",
                (at + timedelta(seconds=1),),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE mood SET p = 0.5")


def test_no_prompt_strings_in_code():
    prompt_lines = {
        line.strip()
        for path in Path("prompts").glob("*.md")
        for line in path.read_text().splitlines()
        if len(line.strip()) >= 40
    }
    for path in Path("src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not any(line in node.value for line in prompt_lines), path
                assert not any(
                    tag in node.value for tag in ("<casual>", "<result>", "<|system|>")
                ), path


def test_no_unzoned_clock_calls_in_application_code():
    for path in Path("src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"utcnow", "utcfromtimestamp"}, path
                if node.func.attr == "now":
                    assert node.args or any(k.arg == "tz" for k in node.keywords), path
                if node.func.attr == "fromtimestamp":
                    assert len(node.args) >= 2 or any(
                        k.arg == "tz" for k in node.keywords
                    ), path
