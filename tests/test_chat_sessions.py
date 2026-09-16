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
    return ChatService(
        database,
        llm,
        retriever,
        context,
        ChatSettings.from_registry(Path("config/settings.yaml")),
        person_id="123",
        summarizer=summarizer,
        facts_extractor=facts,
    )


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
    reopened = SessionStore(service.database)
    assert len(reopened.turns(dm["id"])) == 2
    assert reopened.active("topic")["id"] == topic["id"]


@pytest.mark.parametrize("channel, public_threads", [("topic", 1), ("dm", 0)])
async def test_chat_routes_unknown_and_opens_one_question(
    service, database, day, channel, public_threads
):
    await service.open(channel, at=AT)
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
