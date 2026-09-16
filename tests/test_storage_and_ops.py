"""Contention, batched diagnostics, and durable Telegram pacing contracts."""

import asyncio
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event
from unittest.mock import AsyncMock

import pytest
import structlog
from structlog.testing import capture_logs

from src.core.db import Database
from src.core.ops_log import OpsMirror
from src.core.settings import SettingsRegistry, SQLiteSettingsStore
from src.core.time_utils import now
from src.publish import DeliveryRejected, Destination, OutboxWorker, Publisher

MACHINE = Destination("machine", "ops", -100123, 6)
DIARY = Destination("diary", "mika", -100123, 1, primary=True)


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "operations.sqlite3")
    database.initialize()
    return database


def payloads(database):
    with database.connection() as c:
        return [
            json.loads(row[0])
            for row in c.execute("SELECT payload FROM outbox ORDER BY id")
        ]


def event(mirror, index, *, level=logging.INFO, **values):
    mirror.emit(
        logging.LogRecord(
            "blogai.fixture",
            level,
            "",
            0,
            {"event": "job_completed", "trace_id": f"trace-{index}", **values},
            (),
            None,
        )
    )


def registry(database, *, full=False):
    settings = SettingsRegistry.from_file(
        Path("config/settings.yaml"), store=SQLiteSettingsStore(database)
    )
    if full:
        settings.set("log.verbosity", "full", trace_id="fixture")
    return settings


def test_sqlite_waits_for_short_external_writer_without_retry_storm(database):
    ready = Event()

    def other_process_connection():
        connection = sqlite3.connect(database.path, autocommit=True)
        try:
            connection.execute("BEGIN IMMEDIATE")
            ready.set()
            Event().wait(0.15)
            connection.execute("ROLLBACK")
        finally:
            connection.close()

    with database.connection() as connection, ThreadPoolExecutor(1) as pool:
        future = pool.submit(other_process_connection)
        assert ready.wait(5)
        with capture_logs() as events, database.transaction(connection):
            connection.execute("INSERT INTO topics(name) VALUES ('fixture')")
        future.result(timeout=5)
    assert not any(row["event"] == "db_locked_retry" for row in events)


def test_read_connection_has_no_audit_ddl_and_cannot_write(database):
    with database.connection(readonly=True) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert (
            connection.execute("SELECT count(*) FROM sqlite_temp_master").fetchone()[0]
            == 0
        )
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("INSERT INTO topics(name) VALUES ('forbidden')")


async def test_logs_wait_for_batch_window_and_preserve_each_trace(database):
    clock = [0.0]
    mirror = OpsMirror(
        Publisher(database), MACHINE, registry(database), clock=lambda: clock[0]
    )
    for index in range(4):
        event(mirror, index)
    await mirror.drain()
    assert payloads(database) == []
    clock[0] = 5.0
    await mirror.drain()
    rows = payloads(database)
    assert len(rows) == 1
    assert all(f"trace-{index}" in rows[0]["text"] for index in range(4))
    await mirror.drain(force=True)
    assert len(payloads(database)) == 1


def test_batch_transaction_and_individual_rows_keep_their_traces(database, monkeypatch):
    import src.publish as publish

    transaction, enqueue = database.run_transaction, publish.enqueue_outbox

    def traced_transaction(operation):
        assert structlog.contextvars.get_contextvars()["trace_id"] == "first"
        return transaction(operation)

    def traced_row(connection, **values):
        assert (
            structlog.contextvars.get_contextvars()["trace_id"]
            == values["payload"]["trace_id"]
        )
        return enqueue(connection, **values)

    monkeypatch.setattr(database, "run_transaction", traced_transaction)
    monkeypatch.setattr(publish, "enqueue_outbox", traced_row)
    Publisher(database).enqueue_operations(
        [
            dict(
                key=trace,
                destination=MACHINE,
                trace_id=trace,
                method="message",
                text="Fixture",
            )
            for trace in ("first", "second")
        ]
    )


async def test_logs_flush_full_batches_and_force_partial_on_shutdown(database):
    mirror = OpsMirror(Publisher(database), MACHINE, registry(database))
    for index in range(23):
        event(mirror, index)
    await mirror.drain()
    assert len(payloads(database)) == 2
    await mirror.drain(force=True)
    rows = payloads(database)
    assert len(rows) == 3
    for index in range(23):
        assert sum(f"trace_id: trace-{index}\n" in row["text"] for row in rows) == 1


async def test_full_batch_has_one_attachment_with_private_thoughts_removed(database):
    settings = registry(database, full=True)
    settings.set("log.show_thoughts", "false", trace_id="fixture")
    mirror = OpsMirror(Publisher(database), MACHINE, settings)
    for index in range(3):
        event(mirror, index, thought="hidden", output="complete output " * 1000)
    await mirror.drain(force=True)
    rows = payloads(database)
    assert [row["method"] for row in rows] == ["message", "document"]
    attachment = json.loads(rows[1]["content"])
    assert len(attachment["events"]) == 3
    assert all("thought" not in item for item in attachment["events"])
    assert all(
        item["output"] == "complete output " * 1000 for item in attachment["events"]
    )


async def test_batches_fit_telegram_even_with_astral_characters(database):
    mirror = OpsMirror(Publisher(database), MACHINE, registry(database))
    for index in range(11):
        event(mirror, index, actor="\U0001d400" * 5000)
    await mirror.drain(force=True)
    assert all(
        len(row["text"].encode("utf-16-le")) // 2 <= 4096 for row in payloads(database)
    )


async def test_log_batch_commit_failure_retries_same_ids_without_duplicates(
    database, monkeypatch
):
    publisher = Publisher(database)
    original = publisher.enqueue_operations
    mirror = OpsMirror(publisher, MACHINE, registry(database, full=True))
    event(mirror, 1)
    calls = []

    def committed_then_disconnected(operations):
        calls.append(operations)
        result = original(operations)
        if len(calls) == 1:
            raise RuntimeError("Lost commit acknowledgement")
        return result

    monkeypatch.setattr(publisher, "enqueue_operations", committed_then_disconnected)
    with pytest.raises(RuntimeError):
        await mirror.drain(force=True)
    await mirror.drain(force=True)
    assert calls[0] == calls[1]
    assert len(payloads(database)) == 2


async def test_log_batch_rolls_back_card_if_attachment_cannot_be_saved(
    database, monkeypatch
):
    import src.publish as publish

    publisher = Publisher(database)
    mirror = OpsMirror(publisher, MACHINE, registry(database, full=True))
    event(mirror, 1)
    original = publish.enqueue_outbox

    def fail_attachment(connection, **values):
        if values["payload"]["method"] == "document":
            raise RuntimeError("Attachment write failed")
        return original(connection, **values)

    monkeypatch.setattr(publish, "enqueue_outbox", fail_attachment)
    with pytest.raises(RuntimeError):
        await mirror.drain(force=True)
    assert payloads(database) == []
    monkeypatch.setattr(publish, "enqueue_outbox", original)
    await mirror.drain(force=True)
    assert len(payloads(database)) == 2


def test_delivery_bookkeeping_and_mirror_retries_do_not_feed_the_mirror(database):
    mirror = OpsMirror(Publisher(database), MACHINE, registry(database))
    event(mirror, 1, event="db_row_change", table="telegram_delivery_limits")
    event(mirror, 2, event="db_locked_retry", level=logging.DEBUG)
    event(mirror, 3, event="job_failed", ops_mirror=True, level=logging.ERROR)
    event(mirror, 4, event="job_failed", chat_channel="dm", level=logging.ERROR)
    assert mirror.events.empty()


async def test_group_pacing_is_shared_across_topics_bots_and_worker_restarts(database):
    at = now()
    publisher = Publisher(database)
    for index, destination in enumerate((MACHINE, DIARY)):
        publisher.enqueue_operation(
            str(index),
            destination,
            trace_id="pacing",
            method="message",
            text="Fixture",
            at=at,
        )
    transport = AsyncMock()
    transport.send.return_value = 17
    worker = OutboxWorker(database, transport)
    assert await worker.run_once(at=at) == "sent"
    assert await OutboxWorker(database, transport).run_once(at=at) == "idle"
    assert await worker.run_once(at=at + timedelta(seconds=3)) == "idle"
    assert await worker.run_once(at=at + timedelta(seconds=4)) == "sent"
    assert transport.send.await_count == 2


async def test_flood_wait_blocks_pending_and_new_messages_after_restart(database):
    at = now()
    publisher = Publisher(database)
    publisher.enqueue_operation(
        "first", MACHINE, trace_id="flood", method="message", text="One", at=at
    )
    transport = AsyncMock()
    transport.send.side_effect = [DeliveryRejected("Flood control", retry_after=30), 22]
    assert await OutboxWorker(database, transport).run_once(at=at) == "retry"
    publisher.enqueue_operation(
        "new", MACHINE, trace_id="flood", method="message", text="Two", at=at
    )
    worker = OutboxWorker(Database(database.path), transport)
    assert await worker.run_once(at=at + timedelta(seconds=29)) == "idle"
    assert transport.send.await_count == 1
    assert await worker.run_once(at=at + timedelta(seconds=30)) == "sent"
    assert transport.send.await_count == 2


async def test_flood_wait_uses_response_time_not_request_start(database):
    at = now()
    clock = [at]
    Publisher(database).enqueue_operation(
        "first", MACHINE, trace_id="slow", method="message", text="Fixture", at=at
    )
    transport = AsyncMock()

    async def reject(payload):
        clock[0] = at + timedelta(seconds=10)
        raise DeliveryRejected("Flood control", retry_after=30)

    transport.send.side_effect = reject
    worker = OutboxWorker(database, transport, clock=lambda: clock[0])
    assert await worker.run_once() == "retry"
    assert await worker.run_once(at=at + timedelta(seconds=35)) == "idle"
    transport.send.assert_awaited_once()
    transport.send.side_effect = None
    transport.send.return_value = 42
    assert await worker.run_once(at=at + timedelta(seconds=40)) == "sent"


async def test_machine_backlog_does_not_starve_mika_or_other_chats(database):
    at = now()
    publisher = Publisher(database)
    for index in range(12):
        publisher.enqueue_operation(
            f"log-{index}",
            MACHINE,
            trace_id="backlog",
            method="message",
            text="Log",
            at=at,
        )
    publisher.enqueue_operation(
        "mika", DIARY, trace_id="mika", method="message", text="First post", at=at
    )
    owner = Destination("owner", "ops", 123)
    publisher.enqueue_operation(
        "owner", owner, trace_id="owner", method="message", text="Owner reply", at=at
    )
    transport = AsyncMock()
    transport.send.return_value = 10
    worker = OutboxWorker(database, transport)
    assert await worker.run_once(at=at) == "sent"
    assert transport.send.call_args.args[0]["trace_id"] == "mika"
    assert await worker.run_once(at=at) == "sent"
    assert transport.send.call_args.args[0]["trace_id"] == "owner"


async def test_concurrent_workers_cannot_bypass_group_rate_limit(database):
    at = now()
    publisher = Publisher(database)
    for index in range(4):
        publisher.enqueue_operation(
            str(index),
            MACHINE,
            trace_id="race",
            method="message",
            text="Fixture",
            at=at,
        )
    transport = AsyncMock()
    transport.send.return_value = 21
    results = await asyncio.gather(
        *(OutboxWorker(database, transport).run_once(at=at) for _ in range(4))
    )
    assert results.count("sent") == 1
    assert transport.send.await_count == 1


async def test_idle_or_rate_limited_worker_does_not_start_a_write(
    database, monkeypatch
):
    at = now()
    publisher = Publisher(database)
    transport = AsyncMock()
    transport.send.return_value = 11
    worker = OutboxWorker(database, transport)
    original = database.run_transaction

    def forbidden(operation):
        pytest.fail("Idle polling acquired the SQLite writer lock")

    monkeypatch.setattr(database, "run_transaction", forbidden)
    assert await worker.run_once(at=at) == "idle"
    monkeypatch.setattr(database, "run_transaction", original)
    for index in range(2):
        publisher.enqueue_operation(
            str(index),
            MACHINE,
            trace_id="idle",
            method="message",
            text="Fixture",
            at=at,
        )
    assert await worker.run_once(at=at) == "sent"
    monkeypatch.setattr(database, "run_transaction", forbidden)
    assert await worker.run_once(at=at) == "idle"
    assert transport.send.await_count == 1


async def test_upgrade_preserves_learning_mood_and_delivery_progress(tmp_path):
    from src.orchestrator import Event as LearningEvent
    from src.orchestrator import State
    from src.runner import SQLiteLearningStore

    at = now()
    database = Database(tmp_path / "upgrade.sqlite3")
    database.migrate(target_version=12)
    initial = State(min_articles=3, quiz_threshold=0.6)
    store = SQLiteLearningStore(database, initial)
    store.dispatch(
        LearningEvent(
            "first-article",
            "article",
            "upgrade",
            at,
            article_id="article",
            topic="security",
        )
    )

    def save(connection):
        connection.execute(
            "INSERT INTO nodes(id,name,first_seen) VALUES ('n','Fixture',?)", (at,)
        )
        connection.execute(
            "INSERT INTO mood(at,p,a,d,sleep_debt) VALUES (?,0.1,0.2,0.3,2)", (at,)
        )

    database.run_transaction(save)
    publisher = Publisher(database)
    pending = publisher.enqueue_operation(
        "pending", MACHINE, trace_id="upgrade", method="message", text="Pending", at=at
    )
    completed = publisher.enqueue_operation(
        "completed",
        MACHINE,
        trace_id="upgrade",
        method="message",
        text="Completed",
        at=at,
    )
    database.run_transaction(
        lambda c: c.execute(
            "UPDATE outbox SET sent_at=?,tg_message_id=17,attempts=1,next_try_at=NULL "
            "WHERE id=?",
            (at, completed),
        )
    )
    tables = (
        "nodes",
        "mood",
        "learner_state",
        "learning_events",
        "learning_actions",
        "outbox",
    )

    def snapshot():
        with database.connection(readonly=True) as c:
            return {
                table: [tuple(row) for row in c.execute(f"SELECT * FROM {table}")]
                for table in tables
            }

    before = snapshot()
    Database(database.path).initialize()
    assert snapshot() == before
    assert SQLiteLearningStore(database, initial).state() == store.state()
    transport = AsyncMock()
    transport.send.return_value = 18
    worker = OutboxWorker(database, transport)
    assert await worker.run_once(at=at) == "sent"
    assert await worker.run_once(at=at + timedelta(minutes=1)) == "idle"
    assert transport.send.call_args.args[0]["text"] == "Pending"
    with database.connection(readonly=True) as c:
        assert (
            c.execute(
                "SELECT tg_message_id FROM outbox WHERE id=?", (pending,)
            ).fetchone()[0]
            == 18
        )


async def test_flood_wait_blocks_bot_in_other_chats_but_not_other_bots(database):
    at = now()
    publisher = Publisher(database)
    publisher.enqueue_operation(
        "flood", MACHINE, trace_id="flood", method="message", text="Fixture", at=at
    )
    transport = AsyncMock()
    transport.send.side_effect = [DeliveryRejected("Flood", retry_after=30), 21, 22]
    worker = OutboxWorker(database, transport)
    assert await worker.run_once(at=at) == "retry"
    for destination in (
        Destination("owner", "ops", 123),
        Destination("other", "mika", 456),
    ):
        publisher.enqueue_operation(
            destination.channel,
            destination,
            trace_id=destination.bot,
            method="message",
            text="Fixture",
            at=at,
        )
    assert await worker.run_once(at=at) == "sent"
    assert transport.send.call_args.args[0]["trace_id"] == "mika"
    assert await worker.run_once(at=at + timedelta(seconds=29)) == "idle"
    assert await worker.run_once(at=at + timedelta(seconds=30)) == "sent"
    with database.connection(readonly=True) as c:
        assert c.execute("SELECT sum(attempts) FROM outbox").fetchone()[0] == 3


async def test_quiet_batches_preserve_errors_and_transitions_only(database):
    settings = registry(database)
    settings.set("log.verbosity", "quiet", trace_id="quiet")
    mirror = OpsMirror(Publisher(database), MACHINE, settings)
    event(mirror, 1)
    event(mirror, 2, level=logging.WARNING)
    event(mirror, 3, level=logging.ERROR)
    event(mirror, 4, event="state_transition")
    await mirror.drain(force=True)
    rows = payloads(database)
    assert len(rows) == 1
    assert "trace-1" not in rows[0]["text"] and "trace-2" not in rows[0]["text"]
    assert "trace-3" in rows[0]["text"] and "trace-4" in rows[0]["text"]


async def test_events_arriving_during_failed_batch_stay_in_their_own_batch(
    database, monkeypatch
):
    publisher = Publisher(database)
    original = publisher.enqueue_operations
    mirror = OpsMirror(publisher, MACHINE, registry(database))
    event(mirror, 1)
    attempts = []

    def fail_once(operations):
        attempts.append(operations)
        result = original(operations)
        if len(attempts) == 1:
            event(mirror, 2)
            raise RuntimeError("Lost acknowledgement")
        return result

    monkeypatch.setattr(publisher, "enqueue_operations", fail_once)
    with pytest.raises(RuntimeError):
        await mirror.drain(force=True)
    await mirror.drain(force=True)
    assert attempts[0] == attempts[1]
    rows = payloads(database)
    assert len(rows) == 2
    assert "trace-1" in rows[0]["text"] and "trace-2" not in rows[0]["text"]
    assert "trace-2" in rows[1]["text"]


async def test_concurrent_graph_writes_settings_reads_and_log_batches(database):
    settings = registry(database)
    mirror = OpsMirror(Publisher(database), MACHINE, settings)
    for index in range(30):
        event(mirror, index)

    def write_graph():
        for index in range(20):
            database.run_transaction(
                lambda c, index=index: c.execute(
                    "INSERT INTO nodes(id,name) VALUES (?,?)",
                    (str(index), f"Node {index}"),
                )
            )

    def read_settings():
        for _ in range(40):
            assert settings.get("log.verbosity") == "normal"

    transport = AsyncMock()
    transport.send.side_effect = [1, 2, 3]
    worker = OutboxWorker(database, transport)

    async def deliver():
        for _ in range(20):
            await worker.run_once()

    await asyncio.gather(
        asyncio.to_thread(write_graph),
        asyncio.to_thread(read_settings),
        mirror.drain(force=True),
        deliver(),
    )
    with database.connection(readonly=True) as c:
        assert c.execute("SELECT count(*) FROM nodes").fetchone()[0] == 20
        assert c.execute("SELECT count(*) FROM outbox").fetchone()[0] == 3
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    for seconds in (4, 8, 12):
        await worker.run_once(at=now() + timedelta(seconds=seconds))
    assert transport.send.await_count == 3
