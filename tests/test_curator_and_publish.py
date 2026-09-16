"""Publication contracts, declared before the delivery implementation."""

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import structlog

from src.core.db import Database
from src.core.time_utils import ALMATY
from src.publish import DeliveryRejected, Destination, OutboxWorker, Publisher

AT = datetime(2026, 9, 16, 19, tzinfo=ALMATY)
DIARY = Destination("diary", "mika", -100123, 11, primary=True)
CHANNEL = Destination("channel", "mika", -100124)


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "interfaces.sqlite3")
    database.initialize()
    database.run_transaction(
        lambda c: c.execute(
            "INSERT INTO posts(id, kind, state, text) "
            "VALUES ('p', 'summary', 'draft', ?)",
            ("A validated draft for the publication transport contract.",),
        )
    )
    return database


def rows(database):
    with database.connection() as connection:
        return [
            dict(row) for row in connection.execute("SELECT * FROM outbox ORDER BY id")
        ]


async def test_outbox_worker_restart_does_not_duplicate_telegram(database):
    publisher = Publisher(database)
    first = publisher.enqueue_post("p", [DIARY, CHANNEL], trace_id="trace-1", at=AT)
    assert (
        publisher.enqueue_post("p", [DIARY, CHANNEL], trace_id="trace-1", at=AT)
        == first
    )
    transport = AsyncMock()
    transport.send.side_effect = [100, 101]
    worker = OutboxWorker(database, transport)
    assert await worker.run_once(at=AT) == "sent"
    assert await worker.run_once(at=AT) == "sent"
    assert await OutboxWorker(database, transport).run_once(at=AT) == "idle"
    assert transport.send.await_count == 2
    assert all(row["sent_at"].endswith("Z") for row in rows(database))
    with database.connection() as connection:
        post = connection.execute("SELECT * FROM posts WHERE id='p'").fetchone()
        assert post["state"] == "published" and post["tg_message_id"] == 100


async def test_outbox_crash_after_remote_acceptance_is_not_retried(database):
    Publisher(database).enqueue_post("p", [DIARY], trace_id="trace-1", at=AT)
    calls = []

    async def accepted_but_disconnected(payload):
        calls.append(payload)
        raise TimeoutError("Acceptance is unknown to the sender")

    transport = AsyncMock()
    transport.send.side_effect = accepted_but_disconnected
    assert await OutboxWorker(database, transport).run_once(at=AT) == "uncertain"
    restarted = OutboxWorker(database, transport)
    assert await restarted.run_once(at=AT + timedelta(days=1)) == "idle"
    assert len(calls) == 1 and len(restarted.uncertain()) == 1
    restarted.reconcile(rows(database)[0]["id"], message_id=900, at=AT)
    assert await restarted.run_once(at=AT) == "idle"
    assert rows(database)[0]["tg_message_id"] == 900


async def test_outbox_receipt_commit_failure_does_not_resend(database, monkeypatch):
    Publisher(database).enqueue_post("p", [DIARY], trace_id="trace-1", at=AT)
    transport = AsyncMock()
    transport.send.return_value = 99
    worker = OutboxWorker(database, transport)

    def crash(*args, **kwargs):
        raise RuntimeError("Process stopped before receipt commit")

    monkeypatch.setattr(worker, "reconcile", crash)
    with pytest.raises(RuntimeError):
        await worker.run_once(at=AT)
    assert await OutboxWorker(database, transport).run_once(at=AT) == "idle"
    transport.send.assert_awaited_once()


async def test_outbox_concurrent_workers_claim_once(database):
    Publisher(database).enqueue_post("p", [DIARY], trace_id="trace-1", at=AT)
    transport = AsyncMock()
    transport.send.return_value = 20
    results = await asyncio.gather(
        *(OutboxWorker(database, transport).run_once(at=AT) for _ in range(4))
    )
    assert sorted(results) == ["idle", "idle", "idle", "sent"]
    transport.send.assert_awaited_once()


async def test_outbox_retries_only_confirmed_rejection(database):
    Publisher(database).enqueue_post("p", [DIARY], trace_id="trace-1", at=AT)
    transport = AsyncMock()
    transport.send.side_effect = [DeliveryRejected("Flood control", retry_after=30), 33]
    worker = OutboxWorker(database, transport)
    assert await worker.run_once(at=AT) == "retry"
    assert await worker.run_once(at=AT + timedelta(seconds=29)) == "idle"
    assert await worker.run_once(at=AT + timedelta(seconds=30)) == "sent"
    assert rows(database)[0]["attempts"] == 2


async def test_publication_trace_survives_enqueue_and_worker_restart(database):
    Publisher(database).enqueue_post("p", [DIARY], trace_id="chain-123", at=AT)
    transport = AsyncMock()

    async def send(payload):
        assert payload["trace_id"] == "chain-123"
        assert structlog.contextvars.get_contextvars()["trace_id"] == "chain-123"
        return 34

    transport.send.side_effect = send
    await OutboxWorker(database, transport).run_once(at=AT)
    assert json.loads(rows(database)[0]["payload"])["trace_id"] == "chain-123"


def test_publication_refuses_killed_or_changed_drafts(database):
    publisher = Publisher(database)
    publisher.enqueue_post("p", [DIARY], trace_id="chain-123", at=AT)
    database.run_transaction(
        lambda c: c.execute("UPDATE posts SET text='Changed' WHERE id='p'")
    )
    with pytest.raises(ValueError, match="different payload"):
        publisher.enqueue_post("p", [DIARY], trace_id="chain-123", at=AT)
    database.run_transaction(
        lambda c: c.execute("UPDATE posts SET state='killed' WHERE id='p'")
    )
    with pytest.raises(ValueError, match="publishable"):
        publisher.enqueue_post("p", [DIARY], trace_id="chain-123", at=AT)
    assert len(rows(database)) == 1


def test_publication_rejects_naive_time_before_mutation(database):
    with pytest.raises(ValueError, match="aware"):
        Publisher(database).enqueue_post(
            "p", [DIARY], trace_id="trace", at=AT.replace(tzinfo=None)
        )
    assert rows(database) == []
