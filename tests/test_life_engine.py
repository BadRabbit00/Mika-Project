"""Causal state is independent of prose, transactional and restart safe."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from src.core.db import Database
from src.core.itinerary import Activity
from src.core.time_utils import ALMATY

AT = datetime(2026, 9, 16, 19, tzinfo=ALMATY)


@pytest.fixture
def engine(tmp_path):
    from src.core.life_engine import LifeEngine

    database = Database(tmp_path / "life.sqlite3")
    database.initialize()
    config = YAML(typ="safe").load(Path("config/life_simulation.yaml"))
    config["money"]["initial_cash"] = 1000
    engine = LifeEngine(database, config, Path("config"))
    engine.bootstrap(AT)
    return engine


def home(at=AT):
    return Activity(
        "home", str(at.date()), at, at + timedelta(hours=2), "дом", "rest", "Rest"
    )


@pytest.mark.parametrize(
    "mood,busy,outcome",
    [
        ("calm", False, "help"),
        ("tired", True, "delay"),
        ("strained", False, "refusal"),
        ("tired", False, "lecture"),
    ],
)
def test_mother_outcomes_have_distinct_durable_consequences(
    engine, mood, busy, outcome
):
    engine.observe_person("mother", AT, mood=mood, busy=busy)
    engine.notice(AT)
    task = next(t for t in engine.tasks() if t["kind"] == "mother_contact")
    event = engine.execute(task["id"], at=AT, activity=home())
    assert event["outcome"] == outcome
    state = engine.state()
    if outcome in {"help", "lecture"}:
        assert state["cash"] > 1000
    else:
        assert state["cash"] == 1000
    if outcome == "delay":
        assert any(
            t["kind"] == "mother_followup" and t["status"] == "pending"
            for t in engine.tasks()
        )
    if outcome == "refusal":
        assert any(t["kind"] == "dasha_loan" for t in engine.tasks())
    engine.execute(task["id"], at=AT + timedelta(minutes=1), activity=home())
    assert engine.state() == state
    restarted = type(engine)(engine.database, engine.config, Path("config"))
    restarted.bootstrap(AT + timedelta(days=1))
    assert restarted.state() == state
    assert restarted.person("mother", AT) == engine.person("mother", AT)


def test_task_eligibility_and_money_conservation(engine):
    engine.notice(AT)
    task = next(t for t in engine.tasks() if t["kind"] == "mother_contact")
    away = Activity(
        "class",
        str(AT.date()),
        AT,
        AT + timedelta(hours=1),
        "универ",
        "class",
        "Class",
        subject="Practice",
    )
    assert engine.execute(task["id"], at=AT, activity=away) is None
    engine.observe_person("mother", AT, mood="calm", busy=False)
    engine.execute(task["id"], at=AT, activity=home())
    with engine.database.connection(readonly=True) as c:
        changes = c.execute(
            "SELECT sum(delta) FROM money_ledger WHERE account='cash'"
        ).fetchone()[0]
        assert engine.state()["cash"] == 1000 + changes
        assert (
            c.execute("SELECT count(*) FROM life_effects WHERE kind='mood'").fetchone()[
                0
            ]
            == 1
        )


def test_food_dependency_and_events_do_not_require_publication(engine):
    engine.observe_person("mother", AT, mood="calm", busy=False)
    engine.notice(AT)
    task = next(t for t in engine.tasks() if t["kind"] == "mother_contact")
    event = engine.execute(task["id"], at=AT, activity=home())
    with engine.database.connection(readonly=True) as c:
        row = c.execute(
            "SELECT * FROM life_events WHERE id=?", (event["id"],)
        ).fetchone()
        assert row["post_id"] is None
        assert row["cause_id"] is not None
        assert json.loads(row["payload"])["facts"]
    assert engine.needs()["cash"] == engine.state()["cash"]


def test_income_and_repayment_are_idempotent_and_keep_reserve(engine):
    engine.observe_person("mother", AT, mood="strained", busy=False)
    engine.notice(AT)
    task = next(t for t in engine.tasks() if t["kind"] == "mother_contact")
    engine.execute(task["id"], at=AT, activity=home())
    task = next(t for t in engine.tasks() if t["kind"] == "dasha_loan")
    engine.execute(
        task["id"], at=AT + timedelta(hours=1), activity=home(AT + timedelta(hours=1))
    )
    assert engine.state()["debt"] > 0
    payday = AT.replace(day=25)
    engine.income(payday)
    state = engine.state()
    engine.income(payday)
    assert engine.state() == state
    assert state["cash"] >= 3000 and state["debt"] == 0


def test_naive_life_time_is_rejected(engine):
    with pytest.raises(ValueError, match="aware"):
        engine.notice(AT.replace(tzinfo=None))
