"""Session isolation, exact budgets, citation boundaries, and closure receipts."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from src.chat import ChatService, ChatSettings, SessionStore, validate_facts
from src.core.chat_context import ChatContext
from src.core.context import ContextIsolationError, ContextOverflow
from src.core.db import Database
from src.core.mood import MoodModel
from src.core.ops_log import OpsMirror
from src.core.pad import Mood
from src.core.schedule import SleepWindow
from src.core.settings import SettingsRegistry
from src.core.time_utils import ALMATY
from src.core.world import World
from src.retrieve import RetrievedNode

AT = datetime(2026, 9, 16, 19, tzinfo=ALMATY)


@pytest.fixture
def database(tmp_path):
    result = Database(tmp_path / "chat.sqlite3")
    result.initialize()
    return result


@pytest.fixture
def day():
    return World.from_config(Path("config")).day_context(
        AT,
        sleep=SleepWindow(AT.replace(hour=1), AT.replace(hour=9)),
        sleep_debt=0,
        location="дом",
        road_roll=0.99,
    )


@pytest.fixture
def service(database):
    llm = AsyncMock()
    llm.prompt_tokens.return_value = [1] * 100
    # Token identity matters for the five-gram echo contract.
    llm.tokenize.side_effect = lambda text: list(text.encode("utf-8"))
    llm.generate.return_value = json.dumps(
        {
            "answer": "I had tea by the window and enjoyed the quiet evening. "
            "It was a welcome break after a long day.",
            "cited": [],
            "confident": True,
        }
    )
    retriever = AsyncMock()
    retriever.search.return_value = []
    summarizer = AsyncMock(return_value="A conversation about tea.")
    facts = AsyncMock(return_value=[])
    context = ChatContext(
        Path("prompts"), MoodModel.from_config(Path("config"), epoch=AT)
    )
    result = ChatService(
        database,
        llm,
        retriever,
        context,
        ChatSettings.from_registry(Path("config/settings.yaml")),
        person_id="123",
        summarizer=summarizer,
        facts_extractor=facts,
    )
    result.router = AsyncMock()
    result.router.query.return_value = None
    return result


async def test_closed_chat_does_not_call_model(service, day):
    assert (
        await service.reply(
            "dm",
            "Hello",
            trace_id="closed",
            day=day,
            mood=Mood(0, 0, 0),
            wake_reason="alarm",
            topic="security",
        )
        is None
    )
    service.llm.generate.assert_not_awaited()
    service.retriever.search.assert_not_awaited()


@pytest.mark.parametrize("status_write_fails", [False, True])
async def test_overnight_inbox_reads_at_wake_and_recovers_bound_delivery(
    service, day, monkeypatch, status_write_fails
):
    from dataclasses import replace
    from types import SimpleNamespace

    from src.chat_gateway import ChatGateway
    from src.core.chat_inbox import ChatInbox
    from src.core.schedule import Blackout
    from src.publish import OutboxWorker, Publisher

    asleep = AT.replace(hour=3)
    awake = AT.replace(hour=8)
    clock = [asleep]
    monkeypatch.setattr("src.publish.now", lambda: clock[0])
    session = await service.open("dm", at=asleep - timedelta(hours=1))

    async def context(at=None):
        sleeping = clock[0] == asleep
        return dict(
            day=replace(
                day,
                at=clock[0],
                daypart="morning" if not sleeping else "deep_night",
                activity_id="sleep" if sleeping else "breakfast",
                activity_until=clock[0] + timedelta(hours=1),
                chat_allowed=not sleeping,
                blackout=Blackout(sleeping, "sleep" if sleeping else None),
            ),
            mood=Mood(0, 0, 0),
            wake_reason="alarm",
        )

    async def chat_context():
        return (await context()) | {"topic": "security", "life_state": {"pantry": 8}}

    gateway = ChatGateway(
        service,
        Publisher(service.database),
        SimpleNamespace(owner_id=123),
        chat_context,
    )
    inbox = ChatInbox(gateway, SimpleNamespace(context=context), clock=lambda: clock[0])
    await inbox.accept("dm", "Are you awake?", "overnight", received_at=asleep)
    await inbox.tick()
    service.llm.generate.assert_not_awaited()
    assert not await service._expired(session, awake + timedelta(hours=3))
    clock[0] = awake
    from src.core.itinerary import Activity, Itinerary

    def breakfast(c):
        c.execute(
            "INSERT INTO life_days VALUES (?,?,?,?)",
            (str(awake.date()), "test", "{}", awake),
        )
        Itinerary._insert(
            c,
            Activity(
                "breakfast",
                str(awake.date()),
                awake,
                awake + timedelta(hours=1),
                "дом",
                "breakfast",
                "Breakfast",
            ),
        )

    service.database.run_transaction(breakfast)
    service.llm.generate.return_value = json.dumps(
        dict(answer="Доброе утро! Я спала.", cited=[], confident=True)
    )
    if status_write_fails:
        original_status = inbox._status

        async def failed_status(identity, status):
            if status == "ready":
                raise RuntimeError("Simulated failure after the durable outbox bind")
            await original_status(identity, status)

        monkeypatch.setattr(inbox, "_status", failed_status)
    await inbox.tick()
    await inbox.close()
    with service.database.connection(readonly=True) as c:
        assert c.execute("SELECT status FROM chat_inbox").fetchone()[0] == "ready"
    request = service.llm.generate.call_args.args[0]
    assert '"deferred_reason": "sleep"' in request.user
    assert '"pantry": 8' in request.user
    assert all(turn["role"] == "user" for turn in service.store.turns(session["id"]))
    service.database.run_transaction(
        lambda c: c.execute(
            "UPDATE chat_inbox SET status='generating' WHERE id='overnight'"
        )
    )
    inbox.recover()
    with service.database.connection(readonly=True) as c:
        assert c.execute("SELECT status FROM chat_inbox").fetchone()[0] == "ready"
    worker = OutboxWorker(
        service.database, SimpleNamespace(send=AsyncMock(return_value=42))
    )
    assert await worker.run_once(at=AT + timedelta(days=1)) == "sent"
    await inbox.tick()
    assert service.llm.generate.await_count == 1
    assert (
        len([t for t in service.store.turns(session["id"]) if t["role"] == "mika"]) == 1
    )


async def test_explicit_off_after_session_expiry_disables_automatic_reopening(service):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from src.chat_gateway import ChatGateway
    from src.core.chat_inbox import ChatInbox

    await service.open("dm", at=AT)
    assert await service.expire(at=AT + timedelta(hours=7))
    gateway = ChatGateway(service, Mock(), SimpleNamespace(owner_id=123), None)
    await gateway.handle(
        SimpleNamespace(text="/chat off"), channel="dm", trace_id="off"
    )
    inbox = ChatInbox(gateway, None, clock=lambda: AT + timedelta(hours=8))
    assert not await inbox.accept("dm", "Hello", "after-off")
    assert service.store.active("dm") is None


@pytest.mark.parametrize(
    "answer", ["Hi! I am Mika.", "Yes.", "No", "Okay", "Приветик! Я Мика."]
)
async def test_short_meaningful_chat_reply_is_not_an_empty_post(service, day, answer):
    await service.open("dm", at=AT)
    service.llm.generate.return_value = json.dumps(
        dict(answer=answer, cited=[], confident=True)
    )
    reply = await service.reply(
        "dm",
        "Hello",
        trace_id="short",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    assert reply.text == answer


@pytest.mark.parametrize(
    "answer", ["", "   ", "...", "\u200b\u200b", "<think>hidden</think>"]
)
async def test_empty_chat_output_never_becomes_memory(service, day, answer):
    session = await service.open("dm", at=AT)
    service.llm.generate.return_value = json.dumps(
        dict(answer=answer, cited=[], confident=True)
    )
    with pytest.raises(ValueError):
        await service.reply(
            "dm",
            "Hello",
            trace_id="empty",
            day=day,
            mood=Mood(0, 0, 0),
            wake_reason="alarm",
            topic="security",
        )
    assert all(turn["role"] == "user" for turn in service.store.turns(session["id"]))


async def test_chat_errors_are_not_published_as_mika_replies(service, day):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from src.chat_gateway import ChatGateway
    from src.publish import Destination

    await service.open("dm", at=AT)
    service.llm.generate.return_value = "malformed output"
    publisher, layout = Mock(), Mock(owner_id=123)
    layout.destination.return_value = Destination("chat", "mika", -100123, 4)
    gateway = ChatGateway(
        service,
        publisher,
        layout,
        AsyncMock(
            return_value=dict(
                day=day, mood=Mood(0, 0, 0), wake_reason="alarm", topic="security"
            )
        ),
    )
    await gateway.handle(SimpleNamespace(text="Hello"), channel="dm", trace_id="bad")
    assert not any(
        call.args[1].bot == "mika"
        for call in publisher.enqueue_operation.call_args_list
    )


async def test_validated_chat_draft_is_hidden_until_telegram_receipt(
    service, database, day
):
    from src.publish import Destination, OutboxWorker, Publisher

    session = await service.open("dm", at=AT)
    reply = await service.reply(
        "dm",
        "Hello",
        trace_id="delivery",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    assert all(turn["role"] == "user" for turn in service.store.turns(session["id"]))
    publisher = Publisher(database)
    publisher.enqueue_operation(
        "reply:delivery",
        Destination("chat-dm", "mika", 123),
        trace_id="delivery",
        method="message",
        text=reply.text,
        at=AT,
        chat_reply="delivery",
    )
    transport = AsyncMock()
    transport.send.return_value = 42
    worker = OutboxWorker(database, transport)
    assert await worker.run_once(at=AT) == "sent"
    turns = service.store.turns(session["id"])
    assert [turn["role"] for turn in turns] == ["user", "mika"]
    assert turns[-1]["text"] == reply.text
    assert await worker.run_once(at=AT + timedelta(seconds=2)) == "idle"
    assert len(service.store.turns(session["id"])) == 2


async def test_unknown_delivery_never_enters_chat_memory(service, database, day):
    from src.publish import Destination, OutboxWorker, Publisher

    session = await service.open("dm", at=AT)
    reply = await service.reply(
        "dm",
        "Hello",
        trace_id="uncertain",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    Publisher(database).enqueue_operation(
        "reply:uncertain",
        Destination("chat-dm", "mika", 123),
        trace_id="uncertain",
        method="message",
        text=reply.text,
        at=AT,
        chat_reply="uncertain",
    )
    transport = AsyncMock()
    transport.send.side_effect = TimeoutError("Unknown acceptance")
    worker = OutboxWorker(database, transport)
    assert await worker.run_once(at=AT) == "uncertain"
    assert all(turn["role"] == "user" for turn in service.store.turns(session["id"]))
    uncertain = worker.uncertain()[0]
    worker.reconcile(uncertain["id"], message_id=123, at=AT + timedelta(minutes=2))
    assert len(service.store.turns(session["id"])) == 2


@pytest.mark.parametrize("change", ["text", "channel", "method", "key"])
async def test_chat_delivery_cannot_change_validated_reply(
    service, database, day, change
):
    from src.publish import Destination, Publisher

    await service.open("dm", at=AT)
    reply = await service.reply(
        "dm",
        "Hello",
        trace_id="bound",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    options = dict(
        key="reply:bound",
        destination=Destination("chat-dm", "mika", 123),
        trace_id="bound",
        method="message",
        text=reply.text,
        at=AT,
        chat_reply="bound",
    )
    publisher = Publisher(database)
    if change == "text":
        options["text"] = "An unvalidated replacement"
    elif change == "channel":
        options["destination"] = Destination("chat", "mika", -100123, 4)
    elif change == "method":
        options["method"] = "edit"
    else:
        publisher.enqueue_operation(**options)
        options["key"] = "another:bound"
    with pytest.raises(ValueError):
        publisher.enqueue_operation(**options)


async def test_unsent_reply_is_not_used_by_later_generation(service, day):
    await service.open("dm", at=AT)
    common = dict(day=day, mood=Mood(0, 0, 0), wake_reason="alarm", topic="security")
    draft = await service.reply("dm", "First", trace_id="first", **common)
    await service.reply("dm", "Second", trace_id="second", **common)
    request = service.llm.generate.call_args.args[0]
    assert draft.text not in request.user


async def test_staged_reply_trace_cannot_cross_dialogue_channels(service, day):
    await service.open("dm", at=AT)
    await service.open("topic", at=AT)
    common = dict(day=day, mood=Mood(0, 0, 0), wake_reason="alarm", topic="security")
    await service.reply("dm", "Private question", trace_id="collision", **common)
    with pytest.raises(ValueError, match="another session"):
        await service.reply("topic", "Public question", trace_id="collision", **common)
    assert service.llm.generate.await_count == 1


async def test_upgrade_hides_legacy_unsent_turns_and_their_summary(service, day):
    from src.publish import Destination, OutboxWorker, Publisher

    session = await service.open("dm", at=AT)
    user = service.store.add_user(
        session["id"], "Earlier question", trace_id="legacy", at=AT
    )
    service.store.save_reply(
        session["id"],
        user_id=user["id"],
        text="Unconfirmed legacy draft",
        mode="personal",
        cited=[],
        at=AT,
        trace_id="legacy",
        tokens=100,
        topic="security",
    )
    service.store.checkpoint(
        session["id"],
        {
            "text": "Unconfirmed legacy draft",
            "through_idx": 1,
            "finalized": False,
        },
    )
    assert [turn["role"] for turn in service.store.turns(session["id"])] == ["user"]
    await service.reply(
        "dm",
        "Next question",
        trace_id="after-upgrade",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    request = service.llm.generate.call_args.args[0]
    assert "Unconfirmed legacy draft" not in request.user
    assert "Earlier question" in request.user
    Publisher(service.database).enqueue_operation(
        "legacy:chat",
        Destination("chat-dm", "mika", 123),
        trace_id="legacy",
        method="message",
        text="Unconfirmed legacy draft",
        at=AT,
    )
    transport = AsyncMock()
    transport.send.return_value = 123
    assert await OutboxWorker(service.database, transport).run_once(at=AT) == "sent"
    assert any(
        turn["text"] == "Unconfirmed legacy draft"
        for turn in service.store.turns(session["id"])
    )


async def deliver_reply(service, trace_id, *, channel="dm"):
    """Exercise the real receipt path using a transport with no network access."""
    from src.publish import Destination, OutboxWorker, Publisher

    reply = service.store.staged_reply(trace_id)
    with service.database.connection(readonly=True) as connection:
        sent = connection.execute(
            "SELECT count(*) FROM outbox WHERE sent_at IS NOT NULL"
        ).fetchone()[0]
    delivered_at = AT + timedelta(seconds=sent * 5)
    Publisher(service.database).enqueue_operation(
        "reply:" + trace_id,
        Destination("chat-dm" if channel == "dm" else "chat", "mika", 123),
        trace_id=trace_id,
        method="message",
        text=reply["text"],
        at=AT,
        chat_reply=trace_id,
    )
    transport = AsyncMock()
    transport.send.return_value = 42
    assert (
        await OutboxWorker(service.database, transport).run_once(at=delivered_at)
        == "sent"
    )


async def test_dialogue_channels_and_replays_are_isolated(service, day):
    dm = await service.open("dm", at=AT)
    topic = await service.open("topic", at=AT)
    assert dm["id"] != topic["id"]
    kwargs = dict(day=day, mood=Mood(0, 0, 0), wake_reason="alarm", topic="security")
    first = await service.reply(
        "dm", "Private tea preference", trace_id="dm-1", **kwargs
    )
    assert (
        await service.reply("dm", "Private tea preference", trace_id="dm-1", **kwargs)
        == first
    )
    await service.reply("topic", "Public greeting", trace_id="topic-1", **kwargs)
    assert service.llm.generate.await_count == 2
    request = service.llm.generate.call_args.args[0]
    assert "Private tea preference" not in request.user
    await deliver_reply(service, "dm-1")
    reopened = SessionStore(service.database)
    assert len(reopened.turns(dm["id"])) == 2
    assert reopened.active("topic")["id"] == topic["id"]


@pytest.mark.parametrize("channel, public_threads", [("topic", 1), ("dm", 0)])
async def test_chat_routes_unknown_and_opens_one_question(
    service, database, day, channel, public_threads
):
    await service.open(channel, at=AT)
    service.router.query.return_value = "What is seccomp?"
    service.llm.generate.return_value = json.dumps(
        {
            "answer": "I do not know this yet. Please send me an article about "
            "seccomp so I can start studying it.",
            "cited": [],
            "confident": False,
        }
    )
    result = await service.reply(
        channel,
        "What is seccomp?",
        trace_id="unknown",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    assert result.mode == "unknown" and not result.cited
    await deliver_reply(service, "unknown", channel=channel)
    service.retriever.search.assert_awaited_once_with(
        "What is seccomp?", topic="security"
    )
    request = service.llm.generate.call_args.args[0]
    assert request.profile == "chat_unknown"
    with database.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM threads WHERE kind='question' "
                "AND channel='public'"
            ).fetchone()[0]
            == public_threads
        )
        assert c.execute("SELECT channel FROM threads").fetchone()[0] == (
            "dm" if channel == "dm" else "public"
        )
        assert c.execute("SELECT count(*) FROM nodes").fetchone()[0] == 0


async def test_chat_rejects_citations_outside_retrieved_nodes(service, database, day):
    service.router.query.return_value = "seccomp"
    database.run_transaction(
        lambda c: c.execute("INSERT INTO nodes(id,name) VALUES ('n','seccomp')")
    )
    service.retriever.search.return_value = [
        RetrievedNode("n", "seccomp", "A filter", ())
    ]
    service.llm.generate.return_value = json.dumps(
        {
            "answer": "A fabricated citation must not become a stored answer.",
            "cited": ["invented"],
            "confident": True,
        }
    )
    session = await service.open("dm", at=AT)
    with pytest.raises(ValueError, match="citation"):
        await service.reply(
            "dm",
            "seccomp?",
            trace_id="citation",
            day=day,
            mood=Mood(0, 0, 0),
            wake_reason="alarm",
            topic="security",
        )
    assert all(turn["role"] == "user" for turn in service.store.turns(session["id"]))


def test_chat_mode_blocks_are_isolated(service, day):
    kwargs = dict(
        question="Hello",
        history=[],
        summary="",
        people_facts=[],
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
    )
    with pytest.raises(ContextIsolationError):
        service.context.build("personal", nodes=[{"id": "n"}], **kwargs)
    request = service.context.build(
        "topical", nodes=[{"id": "n", "summary": "Graph evidence"}], **kwargs
    )
    assert "Graph evidence" in request.user
    assert "кофе" not in request.user and "life_state" not in request.user
    assert "people_facts" in request.user


async def test_chat_compresses_only_head_and_preserves_last_twelve(service, day):
    session = await service.open("dm", at=AT)
    for idx in range(14):
        service.store.add_user(
            session["id"], f"turn-{idx}", trace_id=f"seed-{idx}", at=AT
        )
    service.llm.prompt_tokens.side_effect = [[1] * 16001, [1] * 100]
    await service.reply(
        "dm",
        "Newest question",
        trace_id="newest",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    head = service.summarizer.call_args.kwargs["turns"]
    assert [turn["text"] for turn in head] == ["turn-0", "turn-1", "turn-2"]
    request = service.llm.generate.call_args.args[0]
    assert all(f'"turn-{idx}"' in request.user for idx in range(3, 14))
    assert "Newest question" in request.user
    await deliver_reply(service, "newest")
    assert len(service.store.turns(session["id"])) == 16


async def test_chat_overflow_never_truncates_protected_tail(service, day):
    await service.open("dm", at=AT)
    service.llm.prompt_tokens.return_value = [1] * 16001
    with pytest.raises(ContextOverflow):
        await service.reply(
            "dm",
            "Large turn",
            trace_id="large",
            day=day,
            mood=Mood(0, 0, 0),
            wake_reason="alarm",
            topic="security",
        )
    service.llm.generate.assert_not_awaited()
    service.summarizer.assert_not_awaited()


async def test_session_expires_after_six_hours_and_saves_only_direct_facts(
    service, database, day
):
    session = await service.open("dm", at=AT)
    service.store.add_user(session["id"], "My name is Alex.", trace_id="name", at=AT)
    turn = service.store.turns(session["id"])[0]
    service.facts_extractor.return_value = [
        {"kind": "name", "fact": "My name is Alex.", "source": turn["id"]}
    ]
    assert await service.expire(at=AT + timedelta(hours=5, minutes=59)) == []
    assert await service.expire(at=AT + timedelta(hours=6)) == [session["id"]]
    assert service.store.active("dm") is None
    assert await service.expire(at=AT + timedelta(hours=7)) == []
    with database.connection() as c:
        assert (
            c.execute("SELECT fact FROM people_facts").fetchone()[0]
            == "My name is Alex."
        )
        assert c.execute("SELECT kind FROM narrative").fetchone()[0] == "chat"
        assert c.execute("SELECT count(*) FROM nodes").fetchone()[0] == 0
    assert json.loads((await service.export(session["id"])).splitlines()[0])["session"][
        "closed_at"
    ].endswith("Z")


async def test_failed_closure_stays_closed_and_can_finish_after_restart(service):
    session = await service.open("dm", at=AT)
    service.store.add_user(session["id"], "My name is Alex.", trace_id="name", at=AT)
    service.summarizer.side_effect = TimeoutError("Model unavailable")
    with pytest.raises(TimeoutError):
        await service.expire(at=AT + timedelta(hours=6))
    assert service.store.active("dm") is None
    service.summarizer.side_effect = None
    assert await service.expire(at=AT + timedelta(hours=7)) == [session["id"]]
    assert await service.expire(at=AT + timedelta(hours=8)) == []


@pytest.mark.parametrize("unavailable", [False, True])
async def test_session_export_measures_first_to_last_reply_similarity(
    service, day, unavailable
):
    session = await service.open("dm", at=AT)
    for index in range(2):
        await service.reply(
            "dm",
            f"Greeting {index}",
            trace_id=f"voice-{index}",
            day=day,
            mood=Mood(0, 0, 0),
            wake_reason="alarm",
            topic="security",
        )
        await deliver_reply(service, f"voice-{index}")
    service.llm.embed.side_effect = (
        httpx.ConnectError("offline") if unavailable else [[1.0, 0.0], [0.6, 0.8]]
    )
    exported = (await service.export(session["id"])).splitlines()
    metrics = json.loads(exported[0])["metrics"]
    assert len(exported) == 5
    assert metrics["voice_drift_status"] == (
        "embedding_unavailable" if unavailable else "measured"
    )
    if unavailable:
        assert metrics["voice_drift"] is None
    else:
        assert metrics["voice_drift"] == pytest.approx(0.6)
        assert service.llm.embed.await_count == 2


async def test_session_mood_export_uses_normalized_deltas_and_all_band_changes(
    service, day
):
    import math

    start = Mood(-1, -1, -1)
    session = await service.open("dm", at=AT, mood=start)
    samples = [Mood(1, 1, 1), Mood(-1, -1, -1), Mood(1, 1, 1)]
    for index, mood in enumerate(samples):
        await service.reply(
            "dm",
            f"Greeting {index}",
            trace_id=f"mood-{index}",
            day=day,
            mood=mood,
            wake_reason="alarm",
            topic="security",
        )
        await deliver_reply(service, f"mood-{index}")
    await service.close(session["id"], at=AT + timedelta(minutes=5), mood=Mood(1, 1, 1))
    # Reopening the store must preserve intermediate transitions.
    service.store = SessionStore(service.database)
    service.llm.embed.return_value = [1.0, 0.0]
    exported = (await service.export(session["id"])).splitlines()
    metrics = json.loads(exported[0])["metrics"]
    assert metrics["mood_drift"] == pytest.approx(math.sqrt(12) / (2 * math.sqrt(3)))
    assert metrics["mood_delta"] == {"P": 2.0, "A": 2.0, "D": 2.0}
    assert metrics["band_changes"] == 9
    assert '"bands"' not in service.llm.generate.call_args.args[0].user
    assert '"PAD"' not in service.llm.generate.call_args.args[0].user


async def test_chat_uses_setting_overrides_on_next_call(service):
    from src.core.settings import SQLiteSettingsStore

    registry = SettingsRegistry.from_file(
        Path("config/settings.yaml"), store=SQLiteSettingsStore(service.database)
    )
    service._settings = registry
    assert service.settings.ttl_hours == 6
    registry.set("chat.session_ttl_hours", "8", trace_id="setting")
    assert service.settings.ttl_hours == 8


async def test_session_band_changes_include_mood_events_between_replies(service):
    session = await service.open("dm", at=AT, mood=Mood(0, 0, 0))
    service.database.run_transaction(
        lambda c: c.executemany(
            "INSERT INTO mood(at,p,a,d) VALUES (?,?,?,?)",
            [
                (AT + timedelta(minutes=1), -1, -1, -1),
                (AT + timedelta(minutes=2), 1, 1, 1),
            ],
        )
    )
    await service.close(session["id"], at=AT + timedelta(minutes=3), mood=Mood(0, 0, 0))
    metrics = json.loads((await service.export(session["id"])).splitlines()[0])[
        "metrics"
    ]
    assert metrics["mood_drift"] == 0
    assert metrics["band_changes"] == 9


async def test_open_session_does_not_invent_end_mood(service):
    session = await service.open("dm", at=AT, mood=Mood(0, 0, 0))
    metrics = json.loads((await service.export(session["id"])).splitlines()[0])[
        "metrics"
    ]
    assert metrics["mood_drift"] is None
    assert metrics["mood_delta"] is None
    assert metrics["mood_drift_status"] == "insufficient_observations"


def test_fact_filter_rejects_inferences_sensitive_data_and_wrong_speaker():
    turns = [
        {"id": 1, "role": "user", "text": "My name is Alex. My salary is 1000."},
        {"id": 2, "role": "mika", "text": "You are a student."},
    ]
    candidates = [
        {"kind": "name", "fact": "My name is Alex.", "source": 1},
        {"kind": "context", "fact": "My salary is 1000.", "source": 1},
        {"kind": "context", "fact": "You are a student.", "source": 2},
        {"kind": "prefs", "fact": "Interested in security", "source": 1},
    ]
    assert validate_facts(candidates, turns) == candidates[:1]


def test_chat_settings_and_times_are_validated(database):
    settings = ChatSettings.from_registry(Path("config/settings.yaml"))
    assert (settings.budget, settings.keep_last_turns, settings.ttl_hours) == (
        16000,
        12,
        6,
    )
    with pytest.raises(ValueError, match="aware"):
        SessionStore(database).open("dm", at=AT.replace(tzinfo=None))


def test_private_dialogue_events_never_enter_the_group_log_mirror():
    mirror = OpsMirror(
        None, None, SettingsRegistry.from_file(Path("config/settings.yaml"))
    )
    mirror.emit(
        logging.LogRecord(
            "blogai.chat",
            logging.ERROR,
            "",
            0,
            {
                "event": "local_generation_finished",
                "chat_channel": "dm",
                "output": "Private dialogue",
            },
            (),
            None,
        )
    )
    assert mirror.events.empty()


@pytest.mark.parametrize("channel", ["topic", "dm"])
async def test_chat_ingress_uses_mika_and_background_queue(channel):
    from aiogram.types import Chat, Message, User

    from src.bot import BotIngress
    from src.core.tasks import JobQueue
    from src.core.telegram import TelegramLayout

    layout = TelegramLayout(
        owner_id=123,
        group_id=-100123,
        topics=dict(
            diary=1, author=2, curator=3, chat=4, library=5, machine=6, control=7
        ),
    )
    jobs, commands = JobQueue(), AsyncMock()
    ingress = BotIngress(layout, {10: "mika", 30: "ops"}, jobs, commands)
    message = Message(
        message_id=7,
        date=AT,
        chat=Chat(
            id=123 if channel == "dm" else -100123,
            type="private" if channel == "dm" else "supergroup",
        ),
        message_thread_id=None if channel == "dm" else 4,
        from_user=User(id=123, is_bot=False, first_name="Owner"),
        text="/chat on",
    )
    from types import SimpleNamespace

    await ingress.on_message(message, SimpleNamespace(id=10))
    assert jobs.pending == 1
    commands.chat.assert_not_awaited()
    await jobs.start()
    await jobs.join()
    await jobs.close()
    assert commands.chat.call_args.kwargs["channel"] == channel


async def test_mixed_personal_message_does_not_depend_on_graph(service, day):
    await service.open("dm", at=AT)
    service.retriever.search.side_effect = AssertionError("No graph request expected")
    service.llm.generate.return_value = json.dumps(
        dict(
            answer="Потихоньку отдыхаю. А что у тебя сегодня было?",
            cited=[],
            confident=True,
        )
    )
    reply = await service.reply(
        "dm",
        "Веду блог про ИИ агента, сейчас тестирую его. Как настроение у тебя?",
        trace_id="mixed",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    assert reply.mode == "personal"
    service.retriever.search.assert_not_awaited()
    service.router.query.assert_awaited_once()
    assert "people_facts" in service.router.query.call_args.args[0].user
    assert "day_context" in service.llm.generate.call_args.args[0].user


async def test_missing_name_asks_person_without_opening_knowledge_thread(service, day):
    session = await service.open("dm", at=AT)
    service.llm.generate.return_value = json.dumps(
        dict(
            answer="Привет! Не помню, как тебя зовут. Давай знакомиться?",
            cited=[],
            confident=False,
        )
    )
    reply = await service.reply(
        "dm",
        "Привет, а как меня зовут?",
        trace_id="missing-name",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    assert reply.mode == "personal"
    service.retriever.search.assert_not_awaited()
    await deliver_reply(service, "missing-name")
    assert service.store.turns(session["id"])[-1]["text"] == reply.text
    with service.database.connection(readonly=True) as c:
        assert c.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM people_facts").fetchone()[0] == 0


async def test_knowledge_request_can_ask_for_clarification_without_magic_phrase(
    service, day
):
    await service.open("dm", at=AT)
    service.router.query.return_value = "seccomp"
    service.llm.generate.return_value = json.dumps(
        dict(
            answer="Уточни, что именно ты хочешь разобрать? Пришлёшь свой пример?",
            cited=[],
            confident=False,
        )
    )
    reply = await service.reply(
        "dm",
        "seccomp?",
        trace_id="clarify",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    assert reply.mode == "unknown"
    service.retriever.search.assert_awaited_once_with("seccomp", topic="security")


async def test_optional_knowledge_service_failure_still_allows_dialogue(service, day):
    await service.open("dm", at=AT)
    service.router.query.return_value = "seccomp"
    service.retriever.search.side_effect = httpx.ConnectError(
        "Embedding server unavailable"
    )
    service.llm.generate.return_value = json.dumps(
        dict(
            answer="Сейчас не получается проверить заметки. Покажешь свой пример?",
            cited=[],
            confident=False,
        )
    )
    result = await service.reply(
        "dm",
        "Как работает seccomp?",
        trace_id="graph-offline",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    assert result.mode == "unknown"
    assert (
        json.loads(service.llm.generate.call_args.args[0].user)["knowledge_status"]
        == "unavailable"
    )


@pytest.mark.parametrize("delivered", [True, False])
@pytest.mark.parametrize("name_question", ["Как тебя зовут?", "Как к тебе обращаться?"])
async def test_name_answer_is_remembered_only_after_delivered_name_question(
    service, day, delivered, name_question
):
    session = await service.open("dm", at=AT)
    service.llm.generate.return_value = json.dumps(
        dict(answer=name_question, cited=[], confident=False)
    )
    await service.reply(
        "dm",
        "Привет, а как меня зовут?",
        trace_id="ask-name",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    if delivered:
        await deliver_reply(service, "ask-name")
    user = service.store.add_user(
        session["id"], "Лёша", trace_id="name-answer", at=AT + timedelta(minutes=1)
    )
    service.facts_extractor.return_value = [
        {"kind": "name", "fact": "Лёша", "source": user["id"]}
    ]
    await service.close(session["id"], at=AT + timedelta(minutes=2))
    service.store = SessionStore(service.database)
    await service.open("dm", at=AT + timedelta(minutes=3))
    await service.reply(
        "dm",
        "Помнишь моё имя?",
        trace_id="recall-name",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    data = json.loads(service.llm.generate.call_args.args[0].user)
    assert data["people_facts"] == (
        [{"fact": "Лёша", "kind": "name"}] if delivered else []
    )
    assert not data["session_summary"]
    assert all(turn["text"] != "Лёша" for turn in data["dialog_tail"])
    service.retriever.search.assert_not_awaited()


def test_question_about_unknown_name_is_not_itself_a_personal_fact():
    turns = [{"id": 1, "role": "user", "text": "Как меня зовут? Может быть, Лёша?"}]
    assert validate_facts([{"kind": "name", "fact": "Лёша", "source": 1}], turns) == []


async def test_conversational_router_is_bounded_and_file_backed(service, day):
    from src.core.chat_router import ChatRouter

    request = service.context.build(
        "personal",
        question="Как настроение?",
        nodes=[],
        history=[],
        summary="",
        people_facts=[],
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
    )
    router = ChatRouter(service.llm, Path("prompts"))
    service.llm.generate.return_value = '{"knowledge_query": null}'
    assert await router.query(request) is None
    routed = service.llm.generate.call_args.args[0]
    assert routed.profile == "chat_route"
    assert routed.system == Path("prompts/chat_route.md").read_text().strip()
    service.llm.generate.assert_awaited_once()
    assert (
        service.llm.generate.call_args.kwargs["grammar"]
        == Path("grammars/chat_route.gbnf").read_text()
    )
    service.llm.generate.reset_mock()
    service.llm.prompt_tokens.return_value = [1] * 16001
    with pytest.raises(ContextOverflow):
        await router.query(request)
    service.llm.generate.assert_not_awaited()


@pytest.mark.parametrize(
    "raw",
    [
        '{"knowledge_query": " "}',
        '{"knowledge_query": 7}',
        '{"knowledge_query": null, "write": "evil"}',
    ],
)
async def test_conversational_router_rejects_invalid_requests(service, day, raw):
    from src.core.chat_router import ChatRouter

    request = service.context.build(
        "personal",
        question="Hello",
        nodes=[],
        history=[],
        summary="",
        people_facts=[],
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
    )
    service.llm.generate.return_value = raw
    with pytest.raises(ValueError):
        await ChatRouter(service.llm, Path("prompts")).query(request)


async def test_fact_extractor_receives_delivered_question_as_evidence(service):
    from src.core.chat_memory import ChatMemory

    turns = [
        {"id": 1, "role": "mika", "text": "Как тебя зовут?"},
        {"id": 2, "role": "user", "text": "Лёша"},
    ]
    service.llm.generate.return_value = '[{"kind":"name","fact":"Лёша","source":2}]'
    result = await ChatMemory(service.llm, Path("prompts")).facts(
        turns=turns, trace_id="facts"
    )
    request = service.llm.generate.call_args.args[0]
    assert "Как тебя зовут?" in request.user
    assert result == [{"kind": "name", "fact": "Лёша", "source": 2}]


@pytest.mark.parametrize(
    "text,name", [("Меня зовут Лёша.", "Лёша"), ("My name is Alex.", "Alex")]
)
def test_explicit_name_can_be_stored_as_verbatim_name(text, name):
    fact = {"kind": "name", "fact": name, "source": 1}
    assert validate_facts([fact], [{"id": 1, "role": "user", "text": text}]) == [fact]


async def test_retrieved_notes_do_not_prevent_a_clarifying_question(
    service, database, day
):
    database.run_transaction(
        lambda c: c.execute("INSERT INTO nodes(id,name) VALUES ('n','seccomp')")
    )
    service.router.query.return_value = "seccomp"
    service.retriever.search.return_value = [
        RetrievedNode("n", "seccomp", "A filter", ())
    ]
    service.llm.generate.return_value = json.dumps(
        dict(
            answer="Ты про фильтрацию вызовов или конкретный пример?",
            cited=[],
            confident=False,
        )
    )
    await service.open("dm", at=AT)
    reply = await service.reply(
        "dm",
        "А как это?",
        trace_id="clarify-notes",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    assert reply.mode == "topical"
    assert not reply.cited
    await deliver_reply(service, "clarify-notes")
    exported = await service.export(reply.session_id)
    assert (
        json.loads(exported.splitlines()[0])["metrics"]["valid_citation_fraction"]
        is None
    )


async def test_fact_batches_keep_name_question_with_its_answer(service):
    from src.core.chat_memory import ChatMemory

    turns = [
        {"id": 1, "role": "user", "text": "Earlier conversation"},
        {"id": 2, "role": "mika", "text": "Как тебя зовут?"},
        {"id": 3, "role": "user", "text": "Лёша"},
    ]

    async def count(request):
        return [1] * (
            16001 if '"id": 1' in request.user and '"id": 3' in request.user else 100
        )

    service.llm.prompt_tokens.side_effect = count
    service.llm.generate.return_value = "[]"
    await ChatMemory(service.llm, Path("prompts")).facts(
        turns=turns, trace_id="batch-facts"
    )
    requests = [call.args[0] for call in service.llm.generate.call_args_list]
    assert len(requests) == 2
    assert "Лёша" in requests[-1].user and "Как тебя зовут?" in requests[-1].user


async def test_error_payload_never_reaches_next_reply_or_memory_extraction(
    service, day
):
    session = await service.open("dm", at=AT)
    error_text = '{"error": "An unknown answer must acknowledge missing knowledge"}'
    service.llm.generate.return_value = json.dumps(
        dict(answer=error_text, cited=[], confident=False)
    )
    common = dict(day=day, mood=Mood(0, 0, 0), wake_reason="alarm", topic="security")
    with pytest.raises(ValueError, match="structure"):
        await service.reply(
            "dm", "Как настроение?", trace_id="internal-error", **common
        )
    service.llm.generate.return_value = json.dumps(
        dict(answer="Привет! Как тебя зовут?", cited=[], confident=False)
    )
    await service.reply("dm", "Привет", trace_id="after-error", **common)
    for request in (
        service.router.query.call_args.args[0],
        service.llm.generate.call_args.args[0],
    ):
        assert error_text not in request.user
        assert all(
            turn["role"] == "user" for turn in json.loads(request.user)["dialog_tail"]
        )
    await deliver_reply(service, "after-error")
    await service.close(session["id"], at=AT + timedelta(minutes=1))
    turns = service.facts_extractor.call_args.kwargs["turns"]
    assert error_text not in [turn["text"] for turn in turns]
    assert [turn["text"] for turn in turns if turn["role"] == "mika"] == [
        "Привет! Как тебя зовут?"
    ]


async def test_personal_memory_is_scoped_to_the_conversation_owner(service, day):
    service.database.run_transaction(
        lambda c: c.executemany(
            "INSERT INTO people_facts(person_id,fact,kind) VALUES (?,?,'name')",
            [("123", "Лёша"), ("another-person", "Other person's private name")],
        )
    )
    await service.open("dm", at=AT)
    await service.reply(
        "dm",
        "Как меня зовут?",
        trace_id="own-name",
        day=day,
        mood=Mood(0, 0, 0),
        wake_reason="alarm",
        topic="security",
    )
    for request in (
        service.router.query.call_args.args[0],
        service.llm.generate.call_args.args[0],
    ):
        assert json.loads(request.user)["people_facts"] == [
            {"fact": "Лёша", "kind": "name"}
        ]


@pytest.mark.parametrize(
    "kind,text,accepted",
    [
        (
            "project",
            "Веду свой блог про ИИ агента, который учится пользоваться Агентами",
            True,
        ),
        ("context", "Учусь на информационную безопасность", True),
        ("prefs", "Не люблю кофе.", True),
        ("project", "Друг ведёт свой блог про ИИ агента.", False),
        ("prefs", "Друг не любит кофе.", False),
    ],
)
def test_direct_russian_first_person_facts_do_not_require_a_pronoun(
    kind, text, accepted
):
    candidate = dict(kind=kind, fact=text, source=1)
    turns = [dict(id=1, role="user", text=text)]
    assert validate_facts([candidate], turns) == ([candidate] if accepted else [])
