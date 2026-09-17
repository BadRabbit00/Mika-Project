"""Everyday publications and unread chat do not depend on the learner queue."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.chat_store import SessionStore
from src.core.db import Database
from src.core.time_utils import ALMATY
from src.providers import RuntimeProviders
from src.publish import DeliveryExpired, Destination, OutboxWorker, Publisher

AT = datetime(2026, 9, 16, 18, 40, tzinfo=ALMATY)


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "runtime.sqlite3")
    db.initialize()
    return db


async def test_empty_library_does_not_stop_life(database):
    from src.life import LifeRuntime
    from src.writer import WriteResult

    providers = RuntimeProviders(database, Path("config"), clock=lambda: AT)

    async def generate(event, **blocks):
        database.run_transaction(
            lambda c: c.execute(
                "INSERT INTO posts(id,kind,state,text) VALUES (?,'offtop','draft',?)",
                (event["id"], json.loads(event["payload"])["facts"]),
            )
        )
        return WriteResult(event["id"], "draft", "Recorded event", 1)

    runtime = LifeRuntime(
        providers,
        SimpleNamespace(recorded=generate),
        Publisher(database),
        [Destination("diary", "mika", -100, 1, True)],
        clock=lambda: AT,
    )
    try:
        assert await runtime.tick() == "queued"
        await runtime.close()
        with database.connection(readonly=True) as c:
            assert c.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
            assert c.execute("SELECT count(*) FROM sources").fetchone()[0] == 0
        restarted = LifeRuntime(
            providers,
            SimpleNamespace(recorded=generate),
            Publisher(database),
            runtime.destinations,
            clock=lambda: AT,
        )
        restarted.recover()
        await restarted.tick()
        await restarted.close()
        with database.connection(readonly=True) as c:
            assert c.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
    finally:
        await providers.close()


async def test_sleep_keeps_incoming_unread_and_session_open(database):
    from src.core.chat_inbox import ChatInbox

    at = AT.replace(hour=3)
    providers = RuntimeProviders(database, Path("config"), clock=lambda: at)
    store = SessionStore(database)
    session = store.open("dm", at=at - timedelta(hours=1))
    service = SimpleNamespace(database=database, reply=AsyncMock())
    inbox = ChatInbox(SimpleNamespace(service=service), providers, clock=lambda: at)
    try:
        assert await inbox.accept("dm", "Hello", "input-1", received_at=at)
        await inbox.tick()
        from structlog.testing import capture_logs

        with capture_logs() as events:
            await inbox.tick()
            await inbox.tick()
        assert not any(
            e.get("table") == "chat_inbox" and e.get("event") == "db_row_change"
            for e in events
        )
        service.reply.assert_not_awaited()
        with database.connection(readonly=True) as c:
            row = c.execute("SELECT * FROM chat_inbox WHERE id='input-1'").fetchone()
            assert row["session_id"] == session["id"]
            assert row["seen_at"] is None and row["deferred_reason"] == "sleep"
    finally:
        await inbox.close()
        await providers.close()


async def test_stale_delivery_is_cancelled_without_becoming_uncertain(database):
    database.run_transaction(
        lambda c: c.execute(
            "INSERT INTO posts(id,kind,state,text) VALUES ('p','offtop','draft','On "
            "my way home')"
        )
    )
    publisher = Publisher(database)
    publisher.enqueue_post(
        "p", [Destination("diary", "mika", -100, 1, True)], trace_id="life:test", at=AT
    )

    async def expired(payload, at):
        raise DeliveryExpired()

    transport = SimpleNamespace(send=AsyncMock())
    worker = OutboxWorker(database, transport, allowed=expired)
    assert await worker.run_once(at=AT) == "expired"
    assert not worker.uncertain()
    transport.send.assert_not_awaited()


def test_empty_catalogue_is_explicitly_allowed_for_live_life(tmp_path):
    from src.catalogue import Catalogue

    assert Catalogue.load(tmp_path, allow_empty=True).sources == {}


async def test_household_task_takes_time_and_blocks_parallel_study(database):
    from src.life import LifeRuntime

    providers = RuntimeProviders(database, Path("config"), clock=lambda: AT)
    runtime = LifeRuntime(providers, None, None, [], clock=lambda: AT)
    try:
        await providers.context(AT)
        before = providers.life.state()["cash"]
        runtime._advance(AT)
        task = providers.itinerary.current(AT)
        assert task.task_id is not None and not task.can_study
        assert task.ends_at > AT
        assert providers.life.state()["cash"] == before
        later = task.ends_at + timedelta(seconds=1)
        await providers.context(later)
        runtime._advance(later)
        assert providers.life.state()["coffee_stage"] == "repaired"
        assert providers.life.state()["cash"] == before - 700
        runtime._advance(later)
        assert providers.life.state()["cash"] == before - 700
    finally:
        await providers.close()


@pytest.mark.parametrize("kind", ["offtop", "daily", "situation"])
async def test_saved_events_reach_real_writer_and_validation(database, kind):
    from src.core.context import ContextBuilder
    from src.offtop import OfftopGenerator, OfftopPlanner
    from src.validator import OutputValidator
    from src.writer import Writer

    providers = RuntimeProviders(database, Path("config"), clock=lambda: AT)
    llm = AsyncMock()
    llm.prompt_tokens.return_value = [1, 2, 3]
    llm.tokenize.return_value = [4, 5]
    text = (
        "Finally made dinner at home after a long day. The kitchen is quiet and "
        "I can sit down for a little while. I had been looking forward to this "
        "pause between chores; now there is a warm plate on the table and no "
        "reason to rush through it."
    )
    llm.generate.return_value = "<casual>" + text + "</casual>"
    writer = Writer(
        database,
        llm,
        ContextBuilder(Path("prompts"), database=database, mood_model=providers.model),
        OutputValidator(llm, echo_similarity=lambda *_: 0.0),
    )
    generator = OfftopGenerator(
        OfftopPlanner.from_config(database, Path("config")),
        providers.world.world,
        writer,
        providers.weather,
    )
    try:
        blocks = await providers.context(AT)
        providers.life.activity(providers.itinerary.current(AT), AT)
        database.run_transaction(
            lambda c: c.execute("UPDATE life_events SET kind=?", (kind,))
        )
        with database.connection(readonly=True) as c:
            event = dict(c.execute("SELECT * FROM life_events LIMIT 1").fetchone())
        result = await generator.recorded(event, **blocks)
        assert result.status == "draft", result.reasons
        assert result.text == text
        request = llm.generate.call_args.args[0]
        assert request.profile == "write_offtop"
        assert request.min_chars == 200 and request.max_chars == 600
        assert json.loads(event["payload"])["facts"] in request.user
        assert request.tokens_in == 3
        with database.connection(readonly=True) as c:
            assert c.execute("SELECT kind FROM posts").fetchone()[0] == kind
    finally:
        await providers.close()
