"""Mandatory transitions survive breaks, stale prose and process recovery."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.db import Database
from src.core.itinerary import Activity, Itinerary
from src.core.pad import Mood
from src.core.time_utils import ALMATY, from_utc_iso, to_utc_iso

AT = datetime(2026, 9, 16, 20, tzinfo=ALMATY)


@pytest.fixture
def setup(tmp_path):
    from src.providers import RuntimeProviders

    database = Database(tmp_path / "transitions.sqlite3")
    database.initialize()
    providers = RuntimeProviders(database, Path("config"), clock=lambda: AT)
    day = str(AT.date())

    def save(c):
        c.execute("INSERT INTO life_days VALUES (?,?,?,?)", (day, "test", "{}", AT))
        for item in (
            Activity(
                "study",
                day,
                AT - timedelta(hours=1),
                AT + timedelta(hours=2),
                "дом",
                "study",
                "Home study",
            ),
            Activity(
                "rest",
                day,
                AT + timedelta(hours=2),
                AT + timedelta(hours=3),
                "дом",
                "rest",
                "Rest",
            ),
            Activity(
                "sleep",
                day,
                AT + timedelta(hours=3),
                AT + timedelta(hours=4),
                "дом",
                "sleep",
                "Sleep",
            ),
        ):
            Itinerary._insert(c, item)

    database.run_transaction(save)
    return database, providers


def test_break_duration_and_return_survive_restart(setup):
    from src.core.breaks import BreakPlanner

    database, providers = setup
    planner = BreakPlanner(providers)
    saved = planner.start(AT, kind="tea", mood=Mood(0, -0.5, 0), sleep_debt=3)
    assert saved["reason"] == "fatigue"
    end = from_utc_iso(saved["ends_at"])
    assert providers.itinerary.current(AT).kind == "tea_prepare"
    assert providers.itinerary.current(end - timedelta(seconds=1)).kind == "tea_break"
    assert providers.itinerary.current(end).can_study
    repeated = BreakPlanner(providers).start(
        AT, kind="tea", mood=Mood(1, 1, 1), sleep_debt=0
    )
    assert repeated == saved
    with database.connection(readonly=True) as c:
        assert c.execute("SELECT count(*) FROM life_breaks").fetchone()[0] == 1
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("during_break", [False, True])
def test_break_blocks_learning_and_cancellation_explains_ending(setup, during_break):
    from src.core.admission import StudyDeferred, StudyGate
    from src.core.breaks import BreakPlanner
    from src.core.transitions import TransitionJournal

    database, providers = setup
    journal = TransitionJournal(providers)
    journal.observe(AT)
    saved = BreakPlanner(providers).start(
        AT, kind="tea", mood=Mood(0, 0, 0), sleep_debt=0
    )
    journal.observe(AT)
    with pytest.raises(StudyDeferred), StudyGate(database, clock=lambda: AT).session():
        pytest.fail("Learning cannot execute during tea preparation")
    end = AT + timedelta(minutes=5) if during_break else from_utc_iso(saved["ends_at"])
    assert providers.itinerary.adapt(
        end, needs={"ill": True}, cause_id="illness-after-tea"
    )
    journal.observe(end, reason="health")
    with database.connection(readonly=True) as c:
        row = c.execute("SELECT * FROM life_breaks").fetchone()
        assert row["status"] == "cancelled"
        endings = c.execute(
            "SELECT payload FROM activity_transitions WHERE kind='finish_study'"
        ).fetchall()
        assert endings and json.loads(endings[-1][0])["reason"] == "health"


def test_mandatory_transition_bypasses_normal_cadence_and_coalesces(setup):
    from src.core.breaks import BreakPlanner
    from src.core.transitions import TransitionJournal
    from src.life import LifeRuntime

    database, providers = setup
    providers.life_config["publishing"].update(daily_target=[1, 1], max_burst=1)
    database.run_transaction(
        lambda c: c.execute(
            "INSERT INTO posts(id,kind,state,text,published_at) VALUES "
            "('earlier','offtop','published','Earlier post',?)",
            (AT - timedelta(minutes=1),),
        )
    )
    journal = TransitionJournal(providers)
    journal.observe(AT)
    BreakPlanner(providers).start(AT, kind="tea", mood=Mood(0, 0, 0), sleep_debt=0)
    journal.observe(AT)
    database.run_transaction(
        lambda c: c.execute(
            "INSERT INTO life_state VALUES ('life.next_post',?,?)",
            (json.dumps(to_utc_iso(AT + timedelta(days=1))), AT),
        )
    )
    runtime = LifeRuntime(providers, None, None, [], clock=lambda: AT)
    event = runtime._next(AT)
    assert event and json.loads(event["payload"])["mandatory"]
    assert len(json.loads(event["payload"])["transitions"]) == 2
    assert runtime._next(AT)["id"] == event["id"]


def test_new_migration_preserves_existing_life_state(tmp_path):
    database = Database(tmp_path / "upgrade.sqlite3")
    database.migrate(target_version=14)
    database.run_transaction(
        lambda c: c.execute("INSERT INTO life_state VALUES ('saved','42',?)", (AT,))
    )
    database.initialize()
    with database.connection(readonly=True) as c:
        assert (
            c.execute("SELECT value FROM life_state WHERE key='saved'").fetchone()[0]
            == "42"
        )
        assert c.execute("SELECT count(*) FROM activity_transitions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "text", ["Сейчас иду пить чай.", "Завтра встречаюсь с Дашей в ресторане."]
)
def test_unplanned_intentions_are_rejected(setup, text):
    from src.core.activity_claims import activity_conflicts

    _, providers = setup
    evidence = {
        "current": {"kind": "study", "location": "дом"},
        "planned": [],
        "facts": "",
    }
    assert activity_conflicts(text, evidence, Path("config/activity_transitions.yaml"))


def test_fatigue_ends_study_without_promising_a_return(setup):
    from src.core.breaks import BreakPlanner
    from src.core.transitions import TransitionJournal

    database, providers = setup
    journal = TransitionJournal(providers)
    journal.observe(AT)
    planner = BreakPlanner(providers)
    reason = planner.stop_if_exhausted(AT, mood=Mood(0, -0.9, 0), sleep_debt=0)
    assert reason == "fatigue"
    journal.observe(AT, reason=reason)
    assert not providers.itinerary.current(AT).can_study
    assert all(
        not item.can_study for item in providers.itinerary.day(AT) if item.ends_at > AT
    )
    with database.connection(readonly=True) as c:
        last = c.execute(
            "SELECT kind,payload FROM activity_transitions ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert last["kind"] == "finish_study"
        assert json.loads(last["payload"])["reason"] == "fatigue"


def test_pre_sleep_notice_upgrades_an_existing_day_and_never_sends_asleep(setup):
    from src.core.breaks import BreakPlanner
    from src.core.transitions import TransitionJournal

    _, providers = setup
    planner, journal = BreakPlanner(providers), TransitionJournal(providers)
    at = AT + timedelta(hours=2, minutes=50)
    planner.ensure_wind_down(at)
    providers.itinerary.adapt(at, needs={"ill": True}, cause_id="illness-before-bed")
    journal.observe(at)
    assert providers.itinerary.current(at).kind == "wind_down"
    event = journal.next(at)
    assert [item["kind"] for item in json.loads(event["payload"])["transitions"]] == [
        "wind_down"
    ]
    planner.ensure_wind_down(at)
    assert journal.next(at)["id"] == event["id"]
    assert journal.next(AT + timedelta(hours=3)) is None


@pytest.mark.parametrize("late_stage", ["generation", "delivery"])
async def test_slow_generation_rebuilds_history_and_restart_does_not_duplicate(
    setup, late_stage
):
    from src.core.breaks import BreakPlanner
    from src.core.writing_snapshot import WritingSnapshot
    from src.life import LifeRuntime
    from src.live import LiveApplication
    from src.publish import Destination, OutboxWorker, Publisher
    from src.writer import WriteResult

    database, providers = setup
    clock, requests, deliveries = [AT], [], []
    destinations = [Destination("diary", "mika", -100, 1, True)]

    async def render(event, **blocks):
        evidence = json.loads(event["payload"])
        requests.append(evidence)
        snapshot = WritingSnapshot(
            "offtop",
            blocks["day"],
            blocks["mood"],
            blocks["wake_reason"],
            {"recorded_event": evidence},
            {},
        )
        text = (
            "Сейчас иду пить чай."
            if len(requests) == 1
            else "Перерыв закончился. Сейчас учусь дома."
        )
        database.run_transaction(
            lambda c: c.execute(
                "INSERT INTO posts(id,kind,state,text,context_snapshot) VALUES "
                "(?,'offtop','draft',?,?)",
                (event["id"], text, snapshot.encode()),
            )
        )
        if len(requests) == 1 and late_stage == "generation":
            clock[0] = end
        return WriteResult(event["id"], "draft", text, 1)

    def runtime():
        return LifeRuntime(
            providers,
            SimpleNamespace(recorded=render),
            Publisher(database),
            destinations,
            clock=lambda: clock[0],
        )

    first = runtime()
    await providers.context(AT)
    first.transitions.observe(AT)
    saved = BreakPlanner(providers).start(
        AT, kind="tea", mood=Mood(0, 0, 0), sleep_debt=0
    )
    end = from_utc_iso(saved["ends_at"])
    first.transitions.observe(AT)
    event = first._next(AT)
    await first._generate(event)
    if late_stage == "delivery":
        clock[0] = end
        app = SimpleNamespace(providers=providers, life=first)
        transport = SimpleNamespace(send=AsyncMock())
        stale_worker = OutboxWorker(
            database,
            transport,
            allowed=lambda p, at: LiveApplication.allowed(app, p, at),
            clock=lambda: clock[0],
        )
        assert await stale_worker.run_once() == "expired"
        transport.send.assert_not_awaited()
    with database.connection(readonly=True) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM outbox WHERE "
                "json_extract(payload,'$.cancelled_reason') IS NULL"
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT publication_status FROM life_events WHERE id=?", (event["id"],)
            ).fetchone()[0]
            == "expired"
        )
    restarted = runtime()
    restarted.recover()
    restarted.transitions.observe(end)
    fresh = restarted._next(end)
    assert fresh["id"] != event["id"]
    await restarted._generate(fresh)
    assert requests[-1]["current"]["kind"] == "study"
    assert [item["kind"] for item in requests[-1]["transitions"]] == [
        "start_study",
        "pause_study",
        "resume_study",
    ]

    async def send(payload):
        deliveries.append(payload)
        return len(deliveries)

    app = SimpleNamespace(providers=providers, life=restarted)
    clock[0] = end + timedelta(seconds=4)
    worker = OutboxWorker(
        database,
        SimpleNamespace(send=send),
        allowed=lambda p, at: LiveApplication.allowed(app, p, at),
        clock=lambda: clock[0],
    )
    assert await worker.run_once() == "sent"
    again = runtime()
    again.recover()
    again.transitions.observe(end)
    assert again._next(end) is None
    assert await worker.run_once() == "idle"
    assert len(deliveries) == 1
    with database.connection(readonly=True) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM activity_transitions WHERE delivered_at IS NOT "
                "NULL"
            ).fetchone()[0]
            == 3
        )
        assert c.execute("SELECT status FROM life_breaks").fetchone()[0] == "resumed"


@pytest.mark.parametrize(
    "text,accepted",
    [
        ("Сейчас иду пить чай.", False),
        ("Пила чай, а сейчас учусь дома.", True),
        ("Завтра встречаюсь с Дашей в ресторане.", False),
        ("Закончила учёбу, потому что устала.", False),
    ],
)
def test_past_break_does_not_authorize_present_intentions(setup, text, accepted):
    from src.core.activity_claims import activity_conflicts

    evidence = {
        "current": {"kind": "study"},
        "planned": [],
        "transitions": [
            {
                "previous": {"kind": "tea_break"},
                "current": {"kind": "study"},
                "reason": "resume",
            }
        ],
    }
    assert (
        bool(activity_conflicts(text, evidence, "config/activity_transitions.yaml"))
        is not accepted
    )


async def test_real_writer_uses_transition_prompt_and_rejects_invented_actions(setup):
    from src.core.breaks import BreakPlanner
    from src.core.context import ContextBuilder
    from src.core.transitions import TransitionJournal
    from src.offtop import OfftopGenerator, OfftopPlanner
    from src.validator import OutputValidator
    from src.writer import Writer

    database, providers = setup
    journal = TransitionJournal(providers)
    journal.observe(AT)
    BreakPlanner(providers).start(AT, kind="tea", mood=Mood(0, 0, 0), sleep_debt=0)
    journal.observe(AT)
    event = journal.next(AT)
    llm = AsyncMock()
    llm.tokenize.return_value = [1, 2]
    llm.prompt_tokens.return_value = [1, 2, 3]
    valid = (
        "Учебную тетрадь пока отложила: у меня запланирован небольшой перерыв. "
        "Сейчас готовлю чай дома, а потом вернусь к занятиям. Хочется немного "
        "отвлечься и спокойно посидеть с кружкой."
    )
    llm.generate.side_effect = [
        "<casual>" + valid + " Завтра встречаюсь с Дашей в ресторане.</casual>",
        "<casual>" + valid + "</casual>",
    ]
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
    result = await generator.recorded(event, **await providers.context(AT))
    assert result.status == "draft" and result.attempts == 2
    request = llm.generate.call_args.args[0]
    assert (request.min_chars, request.max_chars) == (120, 500)
    header = (
        Path("prompts/write_transition.md").read_text(encoding="utf-8").splitlines()[0]
    )
    assert header in request.system
    assert "unplanned_meeting" in request.system + request.user
    assert request.user.count('"mandatory": true') == 1


def test_uncertain_delivery_retains_obligation_without_retrying_as_new_post(setup):
    from src.core.transitions import TransitionJournal
    from src.life import LifeRuntime
    from src.publish import Destination, OutboxWorker, Publisher

    database, providers = setup
    journal = TransitionJournal(providers)
    journal.observe(AT)
    providers.life.activity(providers.itinerary.current(AT), AT)
    event = journal.next(AT)
    related = json.loads(event["payload"])["related_event_id"]
    database.run_transaction(
        lambda c: c.execute(
            "INSERT INTO posts(id,kind,state,text) VALUES "
            "('p','offtop','draft','Saved transition')"
        )
    )
    database.run_transaction(
        lambda c: c.execute(
            "UPDATE life_events SET post_id='p',publication_status='expired' WHERE "
            "id=?",
            (event["id"],),
        )
    )
    journal.bind(event["id"], "p")
    Publisher(database).enqueue_post(
        "p", [Destination("diary", "mika", -100, 1, True)], trace_id=event["id"], at=AT
    )
    worker = OutboxWorker(database, None)
    claimed = worker._claim(AT)
    assert claimed is not None
    runtime = LifeRuntime(providers, None, None, [], clock=lambda: AT)
    runtime.recover()
    assert runtime._next(AT) is None
    with database.connection(readonly=True) as c:
        assert (
            c.execute("SELECT intent_id FROM activity_transitions").fetchone()[0]
            == event["id"]
        )
        assert c.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
    worker.reconcile(claimed["id"], message_id=42, at=AT)
    assert journal.next(AT) is None
    with database.connection(readonly=True) as c:
        row = c.execute(
            "SELECT post_id,publication_status FROM life_events WHERE id=?", (related,)
        ).fetchone()
        assert tuple(row) == ("p", "published")


async def test_restart_mid_break_preserves_announcements_and_return_time(setup):
    from src.core.breaks import BreakPlanner
    from src.core.transitions import TransitionJournal
    from src.providers import RuntimeProviders

    database, providers = setup
    journal = TransitionJournal(providers)
    journal.observe(AT)
    saved = BreakPlanner(providers).start(
        AT, kind="tea", mood=Mood(0, 0, 0), sleep_debt=0
    )
    journal.observe(AT)
    intent = journal.next(AT)
    at = AT + timedelta(minutes=1)
    reloaded = RuntimeProviders(database, Path("config"), clock=lambda: at)
    try:
        after = TransitionJournal(reloaded)
        after.observe(at)
        assert after.next(at)["id"] == intent["id"]
        assert not reloaded.itinerary.current(at).can_study
        assert reloaded.itinerary.current(from_utc_iso(saved["ends_at"])).can_study
        with database.connection(readonly=True) as c:
            assert (
                c.execute("SELECT ends_at FROM life_breaks").fetchone()[0]
                == saved["ends_at"]
            )
            assert (
                c.execute("SELECT count(*) FROM activity_transitions").fetchone()[0]
                == 2
            )
    finally:
        await reloaded.close()


def test_duration_respects_next_commitment_and_is_utc(setup):
    from src.core.breaks import BreakPlanner

    database, providers = setup
    planner = BreakPlanner(providers)
    at = AT + timedelta(hours=1, minutes=40)
    assert planner.start(at, kind="tea", mood=Mood(0, 0, 0), sleep_debt=0) is None
    with database.connection(readonly=True) as c:
        assert c.execute("SELECT count(*) FROM life_breaks").fetchone()[0] == 0
    saved = planner.start(AT, kind="tea", mood=Mood(0, 0, 0), sleep_debt=0)
    assert saved["starts_at"].endswith("Z") and saved["ends_at"].endswith("Z")


def test_scheduled_end_does_not_claim_fatigue(setup):
    from src.core.transitions import TransitionJournal

    database, providers = setup
    journal = TransitionJournal(providers)
    journal.observe(AT)
    journal.observe(AT + timedelta(hours=2, minutes=10), reason="health")
    with database.connection(readonly=True) as c:
        last = c.execute(
            "SELECT payload FROM activity_transitions WHERE kind='finish_study'"
        ).fetchone()
        assert json.loads(last[0])["reason"] == "scheduled_end"


def test_routine_chores_do_not_replace_a_saved_tea_break(setup):
    from src.core.breaks import BreakPlanner

    _, providers = setup
    pause = BreakPlanner(providers).start(
        AT, kind="tea", mood=Mood(0, 0, 0), sleep_debt=0
    )
    at = from_utc_iso(pause["ends_at"]) - timedelta(minutes=8)
    before = providers.itinerary.current(at)
    assert before.kind == "tea_break"
    assert not providers.itinerary.reserve_task(
        at, {"id": "chore", "kind": "coffee_repair"}
    )
    assert providers.itinerary.current(at) == before


def test_household_task_interrupts_actual_study_notices(setup):
    from src.core.transitions import TransitionJournal

    database, providers = setup
    journal = TransitionJournal(providers)
    journal.observe(AT)
    database.run_transaction(
        lambda c: c.execute(
            "INSERT INTO "
            "life_tasks(id,kind,reason,priority,places,earliest_at,payload) "
            "VALUES ('repair','coffee_repair','leaking_coffee_machine',10,?,?,'{}')",
            (json.dumps(["дом"]), AT),
        )
    )
    task = providers.life.tasks()[0]
    assert providers.itinerary.reserve_task(AT, task)
    journal.observe(AT)
    with database.connection(readonly=True) as c:
        pause = c.execute(
            "SELECT payload FROM activity_transitions WHERE kind='pause_study'"
        ).fetchone()
        assert pause is not None
        assert json.loads(pause[0])["current"]["kind"] == "household_task"


def test_required_posts_do_not_exhaust_the_budget_for_a_real_break_event(setup):
    from src.core.breaks import BreakPlanner
    from src.core.transitions import TransitionJournal
    from src.life import LifeRuntime

    database, providers = setup
    journal = TransitionJournal(providers)
    journal.observe(AT)
    BreakPlanner(providers).start(AT, kind="tea", mood=Mood(0, 0, 0), sleep_debt=0)
    journal.observe(AT)
    providers.life_config["publishing"].update(max_burst=1, daily_target=[1, 1])

    def fixture(c):
        c.execute(
            "INSERT INTO posts(id,kind,state,text,published_at) VALUES "
            "('required','offtop','published','Required announcement',?)",
            (AT,),
        )
        c.execute(
            "UPDATE activity_transitions SET post_id='required',delivered_at=?", (AT,)
        )
        c.execute(
            "INSERT INTO life_events(id,entity,at,kind,payload) VALUES "
            "('observation','observation',?,'situation',?)",
            (AT, json.dumps({"facts": "A recorded event during the break"})),
        )

    database.run_transaction(fixture)
    runtime = LifeRuntime(providers, None, None, [], clock=lambda: AT)
    assert runtime._next(AT)["id"] == "observation"
