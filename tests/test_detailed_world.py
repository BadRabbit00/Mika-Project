"""Detailed world changes take real time and survive retries without model calls."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from src.core.db import Database
from src.core.itinerary import Activity
from src.core.life_engine import LifeEngine
from src.core.time_utils import ALMATY, from_utc_iso

AT = datetime(2026, 9, 17, 15, tzinfo=ALMATY)


@pytest.fixture
def engine(tmp_path):
    db = Database(tmp_path / "world.sqlite3")
    db.initialize()
    config = YAML(typ="safe").load(Path("config/life_simulation.yaml"))
    value = LifeEngine(db, config, Path("config"))
    value.bootstrap(AT)
    return value


def activity(at=AT, *, location="парк", kind="park_rest", minutes=60):
    return Activity(
        "fixture:" + location,
        str(at.date()),
        at,
        at + timedelta(minutes=minutes),
        location,
        kind,
        "Fixture activity",
    )


def test_world_storage_is_additive_and_time_is_aware(engine):
    from src.core.detailed_world import DetailedWorld

    world = DetailedWorld(engine)
    with pytest.raises(ValueError, match="aware"):
        world.advance(activity(), AT.replace(tzinfo=None))
    with engine.database.connection(readonly=True) as c:
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        assert c.execute("SELECT count(*) FROM world_runs").fetchone()[0] == 0


def test_migration_keeps_existing_progress_and_draft(tmp_path):
    database = Database(tmp_path / "upgrade.sqlite3")
    database.migrate(target_version=15)

    def original(c):
        c.execute(
            "INSERT INTO nodes(id,name,summary) "
            "VALUES ('known','Known','Saved knowledge')"
        )
        c.execute(
            "INSERT INTO posts(id,kind,state,text) "
            "VALUES ('post','offtop','draft','Saved draft')"
        )
        c.execute(
            "INSERT INTO life_state VALUES ('life.fixture',?,?)", ('{"cash":12345}', AT)
        )

    database.run_transaction(original)
    database.initialize()
    database.initialize()
    with database.connection(readonly=True) as c:
        assert (
            c.execute("SELECT summary FROM nodes WHERE id='known'").fetchone()[0]
            == "Saved knowledge"
        )
        assert (
            c.execute("SELECT state FROM posts WHERE id='post'").fetchone()[0]
            == "draft"
        )
        assert (
            c.execute(
                "SELECT value FROM life_state WHERE key='life.fixture'"
            ).fetchone()[0]
            == '{"cash":12345}'
        )
        assert c.execute("SELECT count(*) FROM world_runs").fetchone()[0] == 0
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_calendar_and_wallet_survive_restart(engine):
    from src.core.world_people import WorldPeople

    people = WorldPeople(engine)
    first = people.day("dasha", AT)
    assert first == WorldPeople(engine).day("dasha", AT)
    assert all(slot["starts_at"] < slot["ends_at"] for slot in first["slots"])
    assert first["balance"] >= 0
    assert any(slot["busy"] for slot in first["slots"])


def test_recipe_cannot_create_food_without_ingredients(engine):
    from src.core.household import Household

    household = Household(engine)
    before = engine.state()["pantry"]
    assert household.cook("vegetable_rice", AT, cause="empty-fridge") is None
    assert engine.state()["pantry"] == before
    household.shop(AT, cause="basket")
    result = household.cook("vegetable_rice", AT, cause="dinner")
    assert result is not None
    state = engine.state()
    household.cook("vegetable_rice", AT, cause="dinner")
    household.shop(AT, cause="basket")
    assert engine.state() == state


def test_expired_ingredients_are_not_available_for_cooking(engine):
    from src.core.household import Household

    household = Household(engine)
    household.shop(AT, cause="basket")
    later = AT + timedelta(days=10)
    assert household.cook("omelette", later, cause="old-eggs") is None
    household.expire(later)
    state = engine.state()
    household.expire(later)
    assert engine.state() == state


def test_park_steps_take_time_and_purchase_happens_once(engine):
    from src.core.detailed_world import DetailedWorld

    world = DetailedWorld(engine)
    visit = activity(minutes=90)
    run = world.start("park_treat", visit, AT, identity="visit-one")
    before = engine.state()["cash"]
    world.advance(visit, AT, seed=False)
    with engine.database.connection(readonly=True) as c:
        row = c.execute("SELECT * FROM world_runs WHERE id=?", (run,)).fetchone()
        assert from_utc_iso(row["due_at"]) > AT
        assert c.execute("SELECT count(*) FROM world_steps").fetchone()[0] == 0
    world.advance(visit, AT + timedelta(minutes=16), seed=False)
    state = engine.state()
    assert state["cash"] < before
    world.advance(visit, AT + timedelta(minutes=16), seed=False)
    assert engine.state() == state
    assert world.start("park_treat", visit, AT, identity="visit-one") == run


def test_child_event_cannot_outlive_parent_or_run_during_sleep(engine):
    from src.core.detailed_world import DetailedWorld

    world = DetailedWorld(engine)
    short = activity(minutes=2)
    assert world.start("park_treat", short, AT, identity="too-short") is None
    sleep = activity(location="дом", kind="sleep", minutes=120)
    world.advance(sleep, AT)
    with engine.database.connection(readonly=True) as c:
        assert c.execute("SELECT count(*) FROM world_steps").fetchone()[0] == 0


def test_completed_action_commits_once_after_crossing_activity_boundary(engine):
    from src.core.detailed_world import DetailedWorld
    from src.core.itinerary import Itinerary

    park = activity(minutes=20)

    def setup(c):
        c.execute(
            "INSERT INTO life_days VALUES (?,?,?,?)", (park.day, "fixture", "{}", AT)
        )
        Itinerary._insert(c, park)

    engine.database.run_transaction(setup)
    world = DetailedWorld(engine)
    run = world.start("park_treat", park, AT)
    with engine.database.connection(readonly=True) as c:
        due = from_utc_iso(
            c.execute("SELECT due_at FROM world_runs WHERE id=?", (run,)).fetchone()[0]
        )
    later = AT + timedelta(minutes=45)
    home = activity(later, location="дом", kind="rest")
    world.advance(home, later, seed=False)
    cash = engine.state()["cash"]
    world.advance(home, later, seed=False)
    assert engine.state()["cash"] == cash == 22000 - 700
    with engine.database.connection(readonly=True) as c:
        receipt = c.execute(
            "SELECT at FROM life_events WHERE id=?", (run + ":0",)
        ).fetchone()
        assert from_utc_iso(receipt[0]) == due
        assert (
            c.execute(
                "SELECT count(*) FROM money_ledger WHERE event_id=?", (run + ":0",)
            ).fetchone()[0]
            == 1
        )


def test_authorized_cadence_has_no_daily_or_burst_quota():
    config = YAML(typ="safe").load(Path("config/life_simulation.yaml"))["publishing"]
    assert config["busy_gap_minutes"] == [10, 30]
    assert config["rest_gap_minutes"] == [5, 20]
    assert config["daily_target"] is None and config["max_burst"] is None


def test_catalogue_has_branching_multi_stage_scenarios():
    catalogue = YAML(typ="safe").load(Path("config/world_scenarios.yaml"))
    assert len(catalogue["scenarios"]) >= 25
    for name, spec in catalogue["scenarios"].items():
        assert len(spec["nodes"]) >= 3, name
        assert spec["entry"] in spec["nodes"]
        for node in spec["nodes"].values():
            assert node["minutes"][0] > 0
            for choice in node["outcomes"]:
                assert choice.get("next") is None or choice["next"] in spec["nodes"]
                assert choice["facts"].strip()
    details = YAML(typ="safe").load(Path("config/world_details.yaml"))
    for stage in details["health"]["stages"].values():
        assert set(stage) == {"productivity", "facts"}


def test_state_cards_ignore_clock_only_changes(engine):
    from src.core.world_journal import WorldJournal

    journal = WorldJournal(engine.database)
    first = {"activity": "sleep", "cash": 22000}
    assert journal.observe(first, AT, cause="initial")
    assert not journal.observe(first, AT + timedelta(minutes=1), cause="poll")
    assert journal.observe(first | {"cash": 20800}, AT, cause="coffee")
    with engine.database.connection(readonly=True) as c:
        rows = c.execute("SELECT * FROM world_changes ORDER BY sequence").fetchall()
        assert len(rows) == 2
        assert json.loads(rows[-1]["changes"])["cash"] == [22000, 20800]


def test_urgency_grows_exponentially_near_due_date():
    from src.core.life_dynamics import urgency

    assert 0 < urgency(3, 7) < 0.05
    assert urgency(6, 7) > 10 * urgency(3, 7)
    assert urgency(7, 7) == urgency(9, 7) == 1


def test_productivity_respects_health_debt_and_mood(engine):
    from src.core.life_dynamics import productivity, study_minutes

    base = dict(mood={"P": 0, "A": 0, "D": 0}, debt=0, stage="well")
    healthy = productivity(engine.details, **base)
    tired = productivity(engine.details, **(base | {"debt": 8}))
    acute = productivity(engine.details, **(base | {"stage": "acute"}))
    recovered = productivity(engine.details, **(base | {"stage": "recovering"}))
    assert 0 == acute < tired < recovered < healthy <= 1
    assert study_minutes(engine.details, acute) == 0
    assert study_minutes(engine.details, tired) < study_minutes(engine.details, healthy)


def test_low_productivity_pay_is_20_to_30_percent_lower_and_deterministic(engine):
    from src.core.life_dynamics import earned_pay

    for index in range(100):
        receipt = earned_pay(engine.details, 0.2, str(index))
        assert 0.7 <= receipt["actual"] / receipt["base"] <= 0.8
        assert earned_pay(engine.details, 0.2, str(index)) == receipt
    normal = earned_pay(engine.details, 0.8, "normal")
    assert normal["actual"] == normal["base"]


def test_full_outing_includes_real_travel_and_nested_time(engine):
    from random import Random

    from src.core.world_plan import outing_segments

    segments = outing_segments(
        AT,
        AT + timedelta(hours=3),
        Random(3),
        engine.config,
        engine.details,
        cafe_allowed=True,
    )
    assert 110 <= sum(item["minutes"] for item in segments) <= 150
    assert segments[0]["origin"] == segments[-1]["destination"] == "дом"
    assert all(item["minutes"] >= 5 for item in segments)
    assert not outing_segments(
        AT,
        AT + timedelta(minutes=15),
        Random(3),
        engine.config,
        engine.details,
        cafe_allowed=True,
    )


async def test_one_state_message_is_pinned_and_edited_after_delivery(engine):
    from types import SimpleNamespace

    from src.core.world_journal import WorldJournal
    from src.publish import Destination, OutboxWorker

    journal = WorldJournal(
        engine.database, state_destination=Destination("state", "ops", -100, 1266)
    )
    sent, clock = [], [AT]

    async def send(payload):
        sent.append(payload)
        return payload.get("message_id", 321)

    worker = OutboxWorker(
        engine.database, SimpleNamespace(send=send), clock=lambda: clock[0]
    )
    journal.observe({"cash": 22000}, AT, cause="start")
    journal.flush(AT)
    journal.flush(AT)
    assert await worker.run_once(at=clock[0]) == "sent"
    clock[0] += timedelta(seconds=5)
    journal.flush(clock[0])
    assert await worker.run_once(at=clock[0]) == "sent"
    journal.observe({"cash": 20800}, clock[0], cause="coffee")
    clock[0] += timedelta(seconds=5)
    journal.flush(clock[0])
    assert await worker.run_once(at=clock[0]) == "sent"
    WorldJournal(engine.database, state_destination=journal.state_destination).flush(
        clock[0]
    )
    assert [row["method"] for row in sent] == ["document", "pin", "edit_document"]
    assert sent[-1]["message_id"] == 321
    assert json.loads(sent[-1]["content"])["cash"] == 20800


async def test_uncertain_state_send_is_not_replaced(engine):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.core.world_journal import WorldJournal
    from src.publish import Destination, OutboxWorker

    target = Destination("state", "ops", -100, 1266)
    journal = WorldJournal(engine.database, state_destination=target)
    journal.observe({"cash": 1000}, AT, cause="start")
    journal.flush(AT)
    send = AsyncMock(side_effect=TimeoutError("Receipt was lost"))
    worker = OutboxWorker(engine.database, SimpleNamespace(send=send), clock=lambda: AT)
    assert await worker.run_once(at=AT) == "uncertain"
    restarted = WorldJournal(engine.database, state_destination=target)
    restarted.observe({"cash": 900}, AT, cause="expense")
    restarted.flush(AT + timedelta(minutes=1))
    assert await worker.run_once(at=AT + timedelta(minutes=1)) == "idle"
    send.assert_awaited_once()
    with engine.database.connection(readonly=True) as c:
        assert c.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


async def test_edit_state_document_uses_original_message_and_full_dictionary():
    from dataclasses import asdict
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.core.telegram import TelegramTransport
    from src.publish import Destination

    bot = SimpleNamespace(
        edit_message_media=AsyncMock(return_value=SimpleNamespace(message_id=78))
    )
    transport = TelegramTransport({"ops": bot})
    result = await transport.send(
        {
            "destination": asdict(Destination("state", "ops", -100, 1266)),
            "method": "edit_document",
            "message_id": 78,
            "filename": "mika-state.json",
            "content": '{"cash":123}',
            "caption": "Mika state",
        }
    )
    assert result == 78
    args = bot.edit_message_media.await_args.kwargs
    assert args["message_id"] == 78
    assert args["media"].media.data == b'{"cash":123}'


def test_physical_scenarios_do_not_overlap(engine):
    from src.core.detailed_world import DetailedWorld

    world = DetailedWorld(engine)
    visit = activity(minutes=90)
    assert world.start("park_treat", visit, AT, identity="sweet")
    assert world.start("park_sketch", visit, AT, identity="sketch") is None


def test_work_creates_receivable_then_pays_once_after_delay(engine):
    from src.core.detailed_world import DetailedWorld

    world = DetailedWorld(engine)
    shift = activity(location="дом", kind="side_job", minutes=120)
    run = world.start("freelance", shift, AT, productivity=0.2)
    cash = engine.state()["cash"]

    def saved():
        with engine.database.connection(readonly=True) as c:
            return dict(
                c.execute("SELECT * FROM world_runs WHERE id=?", (run,)).fetchone()
            )

    end_brief = from_utc_iso(saved()["due_at"])
    world.advance(shift, end_brief, seed=False)
    world.advance(shift, end_brief + timedelta(minutes=1), seed=False)
    end_work = from_utc_iso(saved()["due_at"])
    assert end_work - end_brief >= timedelta(minutes=45)
    world.advance(shift, end_work, seed=False)
    state = engine.state()
    assert state["cash"] == cash
    owed = next(iter(state["receivables"].values()))
    assert 2450 <= owed["actual"] <= 2800
    due = from_utc_iso(saved()["due_at"])
    assert due - end_work == timedelta(hours=24)
    tomorrow = activity(due - timedelta(minutes=1), location="дом", kind="rest")
    world.advance(tomorrow, due - timedelta(seconds=1), seed=False)
    assert engine.state()["cash"] == cash
    world.advance(tomorrow, due, seed=False)
    paid = engine.state()
    assert paid["cash"] == cash + owed["actual"] and not paid["receivables"]
    DetailedWorld(engine).advance(tomorrow, due, seed=False)
    assert engine.state() == paid


def test_accepted_invitation_reserves_real_travel_before_attendance(engine):
    from src.core.detailed_world import DetailedWorld
    from src.core.itinerary import Itinerary

    start = AT.replace(hour=19)
    home = activity(start, location="дом", kind="rest", minutes=240)
    itinerary = Itinerary(
        engine.database, engine.schedule, engine.config, details=engine.details
    )

    def setup(c):
        c.execute(
            "INSERT INTO life_days(day,seed,config_json,created_at) VALUES (?,?,?,?)",
            (str(start.date()), "fixture", "{}", start),
        )
        itinerary._insert(c, home)
        c.execute(
            "INSERT INTO world_calendars VALUES (?,?,?)",
            ("dasha", str(start.date()), json.dumps({"slots": []})),
        )

    engine.database.run_transaction(setup)
    engine.observe_person("dasha", start, mood="calm", busy=False)
    world = DetailedWorld(engine, itinerary=itinerary)
    run = world.start("invite_dasha", home, start)
    for minute in range(1, 200):
        at = start + timedelta(minutes=minute)
        world.advance(itinerary.current(at), at, seed=False)
    with engine.database.connection(readonly=True) as c:
        meeting = c.execute("SELECT * FROM world_appointments").fetchone()
        assert meeting is not None and meeting["status"] == "attended"
        steps = c.execute(
            "SELECT * FROM world_steps WHERE run_id=? ORDER BY starts_at", (run,)
        ).fetchall()
        assert len(steps) >= 4
        assert json.loads(steps[0]["payload"])["outcome"] == "sent"
        assert any(json.loads(s["payload"])["outcome"] == "met" for s in steps)
    day = itinerary.day(start)
    outward = next(a for a in day if a.kind == "travel" and a.origin == "дом")
    back = next(a for a in day if a.kind == "travel" and a.destination == "дом")
    assert outward.ends_at <= from_utc_iso(meeting["starts_at"])
    assert back.starts_at >= from_utc_iso(meeting["ends_at"])
    assert (back.ends_at - outward.starts_at) >= timedelta(minutes=75)
    assert itinerary.day(start) == Itinerary(
        engine.database, engine.schedule, engine.config
    ).day(start)


def test_recovery_and_morning_preserve_real_time_and_study_admission(engine):
    from src.core.itinerary import Itinerary
    from src.core.schedule import SleepWindow
    from src.core.world_plan import plan_tree

    day = AT.replace(day=16, hour=0)
    sleep = SleepWindow(day + timedelta(hours=1), day + timedelta(hours=7, minutes=50))
    tomorrow = SleepWindow(
        day + timedelta(days=1, hours=1), day + timedelta(days=1, hours=9)
    )
    itinerary = Itinerary(
        engine.database, engine.schedule, engine.config, details=engine.details
    )
    normal = itinerary.build(
        day, sleep, tomorrow, needs={"cash": 22000, "productivity": 0.6}
    )
    kinds = {a.kind for a in normal}
    assert {"hygiene", "packing", "bus_wait", "class"} <= kinds
    legs = [a for a in normal if a.kind == "travel" and a.starts_at.hour < 9]
    assert len(legs) == 3
    assert (legs[-1].ends_at - legs[0].starts_at) == timedelta(minutes=35)
    assert legs[0].ends_at - legs[0].starts_at == timedelta(minutes=5)
    sick = itinerary.build(
        day,
        sleep,
        tomorrow,
        needs={"cash": 22000, "ill": True, "health_stage": "acute", "productivity": 0},
    )
    assert not any(a.can_study or a.kind == "class" for a in sick)
    recovered = itinerary.build(
        day,
        sleep,
        tomorrow,
        needs={
            "cash": 22000,
            "ill": True,
            "health_stage": "recovering",
            "productivity": 0.5,
        },
    )
    assert any(a.can_study and a.starts_at.hour < 17 for a in recovered)
    assert all(a.location == "дом" for a in recovered)

    def contained(node):
        for child in node.get("children", []):
            assert (
                node["starts_at"]
                <= child["starts_at"]
                < child["ends_at"]
                <= node["ends_at"]
            )
            contained(child)

    contained(plan_tree(normal))


async def test_fast_receipt_cannot_be_overwritten_by_generation(engine):
    from types import SimpleNamespace

    from src.life import LifeRuntime
    from src.providers import RuntimeProviders
    from src.publish import Destination, OutboxWorker, Publisher
    from src.writer import WriteResult

    providers = RuntimeProviders(engine.database, Path("config"), clock=lambda: AT)
    await providers.context(AT)
    event_id = providers.life.activity(providers.itinerary.current(AT), AT)["id"]
    with engine.database.connection(readonly=True) as c:
        event = dict(
            c.execute("SELECT * FROM life_events WHERE id=?", (event_id,)).fetchone()
        )

    async def render(event, **blocks):
        engine.database.run_transaction(
            lambda c: c.execute(
                "INSERT INTO posts(id,kind,state,text) "
                "VALUES (?,'offtop','draft','Fixture')",
                (event["id"],),
            )
        )
        return WriteResult(event["id"], "draft", "Fixture", 1)

    class ImmediatePublisher(Publisher):
        def enqueue_post(self, *args, **kwargs):
            ids = super().enqueue_post(*args, **kwargs)
            for identity in ids:
                self.database.run_transaction(
                    lambda c, identity=identity: c.execute(
                        "UPDATE outbox SET attempts=1 WHERE id=?", (identity,)
                    )
                )
                OutboxWorker(self.database, None).reconcile(
                    identity, message_id=42, at=AT + timedelta(minutes=2)
                )
            return ids

    runtime = LifeRuntime(
        providers,
        SimpleNamespace(recorded=render),
        ImmediatePublisher(engine.database),
        [Destination("diary", "mika", -100, 1, True)],
        clock=lambda: AT,
    )
    try:
        await runtime._generate(event)
        with engine.database.connection(readonly=True) as c:
            row = c.execute(
                "SELECT * FROM life_events WHERE id=?", (event_id,)
            ).fetchone()
            assert row["publication_status"] == "published"
            pause = json.loads(row["payload"])["post_gap_minutes"]
            due = from_utc_iso(
                json.loads(
                    c.execute(
                        "SELECT value FROM life_state WHERE key='life.next_post'"
                    ).fetchone()[0]
                )
            )
            assert due == AT + timedelta(minutes=2 + pause)
    finally:
        await runtime.close()
        await providers.close()


async def test_saved_debt_taxi_is_affordable_and_charged_once(engine, tmp_path):
    from src.core.time_utils import to_utc_iso
    from src.providers import RuntimeProviders

    initial = AT.replace(hour=23)
    inputs = tmp_path / "initial.json"
    inputs.write_text(
        json.dumps(
            {
                "initial_mood": {"P": 0, "A": 0, "D": 0},
                "initial_mood_at": to_utc_iso(initial),
                "initial_sleep_debt": 9,
            }
        ),
        encoding="utf-8",
    )
    providers = RuntimeProviders(
        engine.database, Path("config"), inputs, clock=lambda: initial
    )
    providers.sleep.life_details["morning"]["taxi_chance"] = 1
    day = initial.replace(hour=0) + timedelta(days=1)
    try:
        saved = providers.sleep.plan(day.date(), at=initial)
        with engine.database.connection(readonly=True) as c:
            row = c.execute(
                "SELECT value FROM life_state WHERE key=?",
                ("morning:" + str(day.date()),),
            ).fetchone()
            decision = json.loads(row[0])
        assert decision["taxi"] and 0 < decision["extra_minutes"] <= 30
        assert providers.sleep.plan(day.date(), at=initial) == saved
        plan = providers.itinerary.ensure(
            day,
            saved,
            providers.sleep.plan((day + timedelta(days=1)).date(), at=initial),
            needs={"cash": engine.state()["cash"], "productivity": 0.2},
        )
        taxi = next(a for a in plan if a.label == "Поездка на такси в университет")
        assert taxi.ends_at - taxi.starts_at == timedelta(minutes=18)
        assert {"hygiene", "packing"} <= {
            a.kind for a in plan if a.ends_at <= taxi.starts_at
        }
        before = providers.life.state()["cash"]
        providers.life.activity(taxi, taxi.starts_at)
        providers.life.activity(taxi, taxi.starts_at)
        assert providers.life.state()["cash"] == before - 2200
    finally:
        await providers.close()


async def test_nested_action_reaches_shared_context_and_blocks_learning(engine):
    from src.core.admission import StudyDeferred, StudyGate
    from src.core.detailed_world import DetailedWorld
    from src.core.itinerary import Itinerary
    from src.core.writing_snapshot import WritingSnapshot
    from src.providers import RuntimeProviders

    study = activity(location="дом", kind="study", minutes=180)

    def setup(c):
        c.execute(
            "INSERT INTO life_days VALUES (?,?,?,?)", (study.day, "fixture", "{}", AT)
        )
        Itinerary._insert(c, study)

    engine.database.run_transaction(setup)
    providers = RuntimeProviders(engine.database, Path("config"), clock=lambda: AT)
    try:
        assert (await providers.context(AT))["day"].study_allowed
        world = DetailedWorld(engine)
        assert world.start("coursework_backup", study, AT)
        blocks = await providers.context(AT)
        day = blocks["day"]
        assert day.world_action_id and not day.study_allowed
        assert day.activity_until < study.ends_at
        assert (
            engine.public_state(AT)["ongoing_activity"]["label"] == day.activity_label
        )
        with pytest.raises(StudyDeferred, match="physical"):
            StudyGate(engine.database, clock=lambda: AT).check()
        snapshot = WritingSnapshot(
            "offtop", day, blocks["mood"], blocks["wake_reason"], {}, {}
        )
        assert WritingSnapshot.decode(snapshot.encode()) == snapshot
        after = (await providers.context(day.activity_until + timedelta(seconds=1)))[
            "day"
        ]
        assert after.world_action_id is None
    finally:
        await providers.close()


def test_busy_friend_replies_later_and_does_not_appear(engine):
    from src.core.detailed_world import DetailedWorld

    world = DetailedWorld(engine)
    at = AT.replace(hour=11)
    home = activity(at, location="дом", kind="rest", minutes=120)
    identity = world.start("invite_dasha", home, at, identity="invitation")
    world.advance(home, at + timedelta(minutes=5), seed=False)
    with engine.database.connection(readonly=True) as c:
        assert c.execute("SELECT count(*) FROM world_appointments").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM world_steps").fetchone()[0] == 1
        due = from_utc_iso(
            c.execute(
                "SELECT due_at FROM world_runs WHERE id=?", (identity,)
            ).fetchone()[0]
        )
    assert due > at + timedelta(minutes=5)
    world.advance(home, due, seed=False)
    with engine.database.connection(readonly=True) as c:
        row = c.execute(
            "SELECT payload FROM world_steps ORDER BY ends_at DESC LIMIT 1"
        ).fetchone()
        assert json.loads(row[0])["outcome"] == "busy"
        assert c.execute("SELECT count(*) FROM world_appointments").fetchone()[0] == 0
        assert (
            c.execute("SELECT count(*) FROM life_effects WHERE kind='mood'").fetchone()[
                0
            ]
            == 1
        )
