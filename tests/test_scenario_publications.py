"""Social outcomes are durable decisions, not repeated publication prompts."""

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from ruamel.yaml import YAML

from src.core.db import Database
from src.core.detailed_world import DetailedWorld
from src.core.itinerary import Activity
from src.core.life_engine import LifeEngine
from src.core.time_utils import ALMATY, from_utc_iso
from src.life import LifeRuntime

AT = datetime(2026, 9, 17, 15, tzinfo=ALMATY)


@pytest.fixture
def world(tmp_path):
    database = Database(tmp_path / "decisions.sqlite3")
    database.initialize()
    engine = LifeEngine(
        database,
        YAML(typ="safe").load(Path("config/life_simulation.yaml")),
        Path("config"),
    )
    engine.bootstrap(AT)
    return DetailedWorld(engine)


def home():
    return Activity(
        "home", str(AT.date()), AT, AT + timedelta(hours=4), "дом", "rest", "Rest"
    )


def step(world, run):
    with world.database.connection(readonly=True) as c:
        row = c.execute("SELECT * FROM world_runs WHERE id=?", (run,)).fetchone()
    world.advance(home(), from_utc_iso(row["due_at"]), seed=False)


def runtime(world):
    result = LifeRuntime.__new__(LifeRuntime)
    result.database, result.engine, result.details = (
        world.database,
        world.engine,
        world,
    )
    result.providers = SimpleNamespace(life_config=world.engine.config)
    result.transitions = SimpleNamespace(next=lambda at: None)
    return result


@pytest.mark.parametrize("person", ["dasha", "aika", "timur"])
def test_no_window_changes_state_before_publication(world, person):
    spec = copy.deepcopy(world.catalogue["invite_" + person])
    spec["entry"] = "agree"
    run = world.start("invite_" + person, home(), AT, spec=spec)
    step(world, run)
    state = world.engine.state()
    decision = state["social_plans"][person]
    assert decision["status"] == "no_window"
    assert decision["event_id"] == run + ":0"
    with world.database.connection(readonly=True) as c:
        event = c.execute(
            "SELECT * FROM life_events WHERE id=?", (run + ":0",)
        ).fetchone()
        assert event["publication_status"] == "pending"
        assert "social_plans" in json.loads(event["payload"])["world"]["changes"]
        action = world.active_action(c, home().id, from_utc_iso(event["at"]))
        assert action["label"] == spec["nodes"]["alternative"]["label"]
        assert action["label"] != spec["label"]
    assert world.engine.public_state()["social_plans"][person] == decision


def test_reflection_is_silent_without_losing_effects_or_home_decision(world):
    spec = copy.deepcopy(world.catalogue["invite_timur"])
    spec["entry"] = "alternative"
    run = world.start("invite_timur", home(), AT, spec=spec)
    step(world, run)
    state = world.engine.state()
    assert state["social_plans"]["timur"]["status"] == "home_alternative"
    with world.database.connection(readonly=True) as c:
        at = from_utc_iso(c.execute("SELECT at FROM life_events").fetchone()[0])
        assert world.active_action(c, home().id, at) is None
    step(world, run)
    with world.database.connection(readonly=True) as c:
        rows = c.execute("SELECT * FROM life_events ORDER BY at,id").fetchall()
        assert [r["publication_status"] for r in rows] == ["pending", "silent"]
        assert c.execute("SELECT count(*) FROM world_steps").fetchone()[0] == 2
        assert (
            c.execute(
                "SELECT count(*) FROM life_effects WHERE event_id=?", (run + ":1",)
            ).fetchone()[0]
            == 1
        )
    before = world.engine.state()
    step(world, run)
    assert world.engine.state() == before
    assert runtime(world)._next(AT + timedelta(hours=2))["id"] == run + ":0"


def test_pending_story_is_chronological_even_when_first_step_is_retrying(world):
    spec = copy.deepcopy(world.catalogue["invite_timur"])
    spec["entry"] = "agree"
    run = world.start("invite_timur", home(), AT, spec=spec)
    step(world, run)
    step(world, run)
    value = runtime(world)
    later = AT + timedelta(hours=2)
    assert value._next(later)["id"] == run + ":0"
    world.database.run_transaction(
        lambda c: c.execute(
            "UPDATE life_events SET retry_at=? WHERE id=?",
            (later + timedelta(minutes=5), run + ":0"),
        )
    )
    assert value._next(later) is None
    world.database.run_transaction(
        lambda c: c.execute(
            "UPDATE life_events SET publication_status='published' WHERE id=?",
            (run + ":0",),
        )
    )
    assert value._next(later)["id"] == run + ":1"


def test_restart_updates_old_publication_policy_without_replaying_outcomes(world):
    spec = copy.deepcopy(world.catalogue["invite_timur"])
    spec["entry"] = "alternative"
    for node in spec["nodes"].values():
        node.pop("publication", None)
        node.pop("label", None)
        if node is spec["nodes"]["remember"]:
            node.pop("passive", None)
        for outcome in node["outcomes"]:
            outcome.pop("social_plan", None)
    run = world.start("invite_timur", home(), AT, spec=spec)
    step(world, run)
    with world.database.connection(readonly=True) as c:
        old = dict(c.execute("SELECT * FROM world_runs WHERE id=?", (run,)).fetchone())
    restarted = runtime(DetailedWorld(world.engine))
    restarted.recover()
    with world.database.connection(readonly=True) as c:
        new = dict(c.execute("SELECT * FROM world_runs WHERE id=?", (run,)).fetchone())
        assert new["due_at"] == old["due_at"]
        assert new["node"] == old["node"]
        assert json.loads(new["payload"])["step"] == json.loads(old["payload"])["step"]
        assert json.loads(new["payload"])["spec"]["nodes"]["remember"]["passive"]
        assert c.execute("SELECT count(*) FROM world_steps").fetchone()[0] == 1
    step(restarted.details, run)
    restarted.recover()
    assert restarted._next(AT + timedelta(hours=2))["id"] == run + ":0"
    assert world.engine.state()["social_plans"]["timur"]["status"] == "home_alternative"


def test_legacy_pending_reflection_is_not_another_post(world):
    spec = copy.deepcopy(world.catalogue["invite_timur"])
    spec["entry"] = "remember"
    spec["nodes"]["remember"].pop("publication", None)
    run = world.start("invite_timur", home(), AT, spec=spec)
    step(world, run)
    runtime(world).recover()
    assert runtime(world)._next(AT + timedelta(hours=2)) is None
    with world.database.connection(readonly=True) as c:
        assert (
            c.execute(
                "SELECT publication_status FROM life_events WHERE id=?", (run + ":0",)
            ).fetchone()[0]
            == "silent"
        )
        assert c.execute("SELECT count(*) FROM life_effects").fetchone()[0] == 1


def test_unpublished_waiting_is_superseded_by_actual_reply(world):
    world.engine.observe_person("timur", AT, mood="calm", busy=False)
    world.database.run_transaction(
        lambda c: c.execute(
            "INSERT INTO world_calendars VALUES (?,?,?)",
            ("timur", str(AT.date()), json.dumps({"slots": []})),
        )
    )
    run = world.start("invite_timur", home(), AT)
    step(world, run)
    assert runtime(world)._next(AT + timedelta(hours=2))["id"] == run + ":0"
    step(world, run)
    with world.database.connection(readonly=True) as c:
        assert (
            c.execute(
                "SELECT publication_status FROM life_events WHERE id=?", (run + ":0",)
            ).fetchone()[0]
            == "expired"
        )
    assert not world.publishable(run + ":0")
    assert runtime(world)._next(AT + timedelta(hours=2))["id"] == run + ":1"
    step(world, run)
    assert not world.publishable(run + ":1")
    assert runtime(world)._next(AT + timedelta(hours=2))["id"] == run + ":2"


async def test_old_queued_reflection_never_reaches_transport(world):
    from unittest.mock import AsyncMock

    from src.live import LiveApplication
    from src.publish import Destination, OutboxWorker, Publisher

    spec = copy.deepcopy(world.catalogue["invite_timur"])
    spec["entry"] = "remember"
    spec["nodes"]["remember"].pop("publication", None)
    run = world.start("invite_timur", home(), AT, spec=spec)
    step(world, run)
    identity = run + ":0"

    def draft(c):
        c.execute(
            "INSERT INTO posts(id,kind,state,text) "
            "VALUES (?,'situation','draft','Saved reflection')",
            (identity,),
        )
        c.execute("UPDATE life_events SET post_id=? WHERE id=?", (identity, identity))

    world.database.run_transaction(draft)
    Publisher(world.database).enqueue_post(
        identity,
        [Destination("diary", "mika", -100, 1, True)],
        trace_id=identity,
        at=AT,
    )
    life = runtime(world)
    life.recover()
    day = SimpleNamespace(blackout=SimpleNamespace(blocked=False))
    app = LiveApplication.__new__(LiveApplication)
    app.providers = SimpleNamespace(
        life=world.engine, context=AsyncMock(return_value={"day": day})
    )
    app.life = life
    transport = SimpleNamespace(send=AsyncMock())
    worker = OutboxWorker(world.database, transport, allowed=app.allowed)
    assert await worker.run_once(at=AT + timedelta(hours=1)) == "expired"
    transport.send.assert_not_awaited()
    assert not worker.uncertain()


async def test_combined_required_transition_survives_silent_related_event(world):
    from unittest.mock import AsyncMock

    from src.live import LiveApplication

    spec = copy.deepcopy(world.catalogue["invite_timur"])
    spec["entry"] = "remember"
    run = world.start("invite_timur", home(), AT, spec=spec)
    step(world, run)

    def combined(c):
        c.execute(
            "INSERT INTO posts(id,kind,state,text) "
            "VALUES ('combined','offtop','draft','Activity changed')"
        )
        c.execute("UPDATE life_events SET post_id='combined' WHERE id=?", (run + ":0",))
        c.execute(
            "INSERT INTO life_events(id,entity,at,kind,payload,post_id) "
            "VALUES ('notice','notice',?,'transition',?,'combined')",
            (AT, json.dumps({"mandatory": True, "related_event_id": run + ":0"})),
        )

    world.database.run_transaction(combined)
    app = LiveApplication.__new__(LiveApplication)
    app.life = runtime(world)
    app.providers = SimpleNamespace(
        life=world.engine,
        context=AsyncMock(
            return_value={
                "day": SimpleNamespace(blackout=SimpleNamespace(blocked=False))
            }
        ),
    )
    assert await app.allowed({"post_id": "combined"}, AT)


def test_observation_without_resource_change_can_still_be_published(world):
    spec = copy.deepcopy(world.catalogue["invite_timur"])
    spec["entry"] = "remember"
    spec["nodes"]["remember"]["publication"] = "event"
    world.catalogue["fixture_observation"] = spec
    run = world.start("fixture_observation", home(), AT)
    step(world, run)
    assert world.publishable(run + ":0")
    assert runtime(world)._next(AT + timedelta(hours=2))["id"] == run + ":0"


def test_restart_keeps_delivered_history_and_does_not_reannounce_older_step(world):
    spec = copy.deepcopy(world.catalogue["invite_timur"])
    spec["entry"] = "agree"
    run = world.start("invite_timur", home(), AT, spec=spec)
    step(world, run)
    step(world, run)
    step(world, run)
    # Reproduce the old queue's reversed delivery order.
    world.database.run_transaction(
        lambda c: c.execute(
            "UPDATE life_events SET publication_status='published' WHERE id=?",
            (run + ":2",),
        )
    )
    runtime(world).recover()
    assert runtime(world)._next(AT + timedelta(hours=2)) is None
    assert not world.publishable(run + ":0")
    with world.database.connection(readonly=True) as c:
        assert (
            c.execute(
                "SELECT publication_status FROM life_events WHERE id=?", (run + ":2",)
            ).fetchone()[0]
            == "published"
        )


async def test_reply_during_generation_cancels_waiting_draft(world):
    from unittest.mock import AsyncMock, Mock

    from src.writer import WriteResult

    run = world.start("invite_timur", home(), AT)
    step(world, run)
    with world.database.connection(readonly=True) as c:
        event = dict(
            c.execute("SELECT * FROM life_events WHERE id=?", (run + ":0",)).fetchone()
        )
    value = runtime(world)
    day = SimpleNamespace(
        at=AT,
        location="дом",
        activity_id=home().id,
        world_action_id=None,
        blackout=SimpleNamespace(blocked=False),
        busy=False,
    )
    value.providers.context = AsyncMock(return_value={"day": day})
    value.providers.itinerary = SimpleNamespace(current=lambda at: home())
    value.clock = lambda: AT + timedelta(hours=1)
    value.destinations = []
    value.publisher = SimpleNamespace(enqueue_post=Mock())

    async def render(event, **blocks):
        step(world, run)
        world.database.run_transaction(
            lambda c: c.execute(
                "INSERT INTO posts(id,kind,state,text) "
                "VALUES ('waiting','situation','draft','Waiting for a reply')"
            )
        )
        return WriteResult("waiting", "draft", "Waiting for a reply", 1)

    value.generator = SimpleNamespace(recorded=AsyncMock(side_effect=render))
    await value._generate(event)
    value.generator.recorded.assert_awaited_once()
    value.publisher.enqueue_post.assert_not_called()
    with world.database.connection(readonly=True) as c:
        assert (
            c.execute("SELECT state FROM posts WHERE id='waiting'").fetchone()[0]
            == "killed"
        )
