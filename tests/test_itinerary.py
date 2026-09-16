"""Complete persisted days, real journeys and reproducible availability."""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from src.core.db import Database
from src.core.schedule import Schedule, SleepWindow
from src.core.time_utils import ALMATY, from_utc_iso

AT = datetime(2026, 9, 16, tzinfo=ALMATY)


@pytest.fixture
def config():
    return YAML(typ="safe").load(Path("config/life_simulation.yaml").read_text())


@pytest.fixture
def itinerary(tmp_path, config):
    from src.core.itinerary import Itinerary

    database = Database(tmp_path / "itinerary.sqlite3")
    database.initialize()
    return Itinerary(database, Schedule.from_config(Path("config")), config)


def windows(day):
    return (
        SleepWindow(day.replace(hour=1), day.replace(hour=7, minute=50)),
        SleepWindow(
            day + timedelta(days=1, hours=1), day + timedelta(days=1, hours=10)
        ),
    )


def test_day_has_no_gaps_overlaps_or_teleportation(itinerary):
    previous = None
    for number in range(7):
        day = AT + timedelta(days=number)
        activities = itinerary.ensure(day, *windows(day))
        assert activities[0].starts_at == day
        assert activities[-1].ends_at == day + timedelta(days=1)
        for item in activities:
            assert item.starts_at.tzinfo == ALMATY
            assert item.ends_at > item.starts_at
            if previous:
                assert previous.ends_at == item.starts_at
                origin = (
                    previous.destination
                    if previous.kind == "travel"
                    else previous.location
                )
                assert (
                    item.origin if item.kind == "travel" else item.location
                ) == origin
            previous = item
            assert item.can_study == (item.kind == "study" and item.location == "дом")
            assert item.can_chat == item.can_publish == (item.kind != "sleep")
            if item.kind == "travel":
                assert item.origin != item.destination


def test_current_subject_and_return_journey_follow_actual_timetable(itinerary):
    itinerary.ensure(AT, *windows(AT))
    lesson = itinerary.current(AT.replace(hour=14, minute=30))
    assert lesson.location == "универ"
    assert lesson.subject == "практикум"
    road = itinerary.current(AT.replace(hour=15))
    assert road.kind == "travel" and road.destination == "дом"
    assert road.ends_at - road.starts_at == timedelta(minutes=35)
    assert itinerary.current(AT.replace(hour=20)).can_study


def test_persisted_plan_survives_new_seed_and_restart(itinerary):
    from src.core.itinerary import Itinerary

    original = itinerary.ensure(AT, *windows(AT))
    changed = dict(itinerary.config, seed="a completely different seed")
    reopened = Itinerary(itinerary.database, itinerary.schedule, changed)
    assert reopened.ensure(AT, *windows(AT), needs={"ill": True}) == original
    with itinerary.database.connection(readonly=True) as connection:
        assert connection.execute("SELECT count(*) FROM life_days").fetchone()[0] == 1
        for row in connection.execute("SELECT starts_at,ends_at FROM life_activities"):
            assert all(value.endswith("Z") for value in row)
            assert from_utc_iso(row[1]) > from_utc_iso(row[0])


def test_illness_changes_plan_and_food_need_selects_shop(itinerary):
    illness = itinerary.ensure(AT, *windows(AT), needs={"ill": True})
    assert all(item.location == "дом" and not item.can_study for item in illness)
    tomorrow = AT + timedelta(days=1)
    groceries = itinerary.ensure(
        tomorrow,
        *windows(tomorrow),
        needs={
            "groceries": True,
            "cash": 5000,
        },
    )
    assert any(item.kind == "shop" for item in groceries)
    assert not any(item.kind in {"cafe", "gym"} for item in groceries)


def test_sleep_prevents_all_persona_activity_and_late_wake_never_teleports(itinerary):
    sleep, following = windows(AT)
    late = replace(sleep, wake=AT.replace(hour=9, minute=15))
    activities = itinerary.ensure(AT, late, following)
    assert itinerary.current(AT.replace(hour=8)).kind == "sleep"
    first_road = next(item for item in activities if item.kind == "travel")
    first_class = next(item for item in activities if item.kind == "class")
    assert first_road.starts_at >= late.wake
    assert first_class.starts_at >= first_road.ends_at
    assert first_road.ends_at - first_road.starts_at == timedelta(minutes=35)


def test_itinerary_requires_aware_instants(itinerary):
    with pytest.raises(ValueError, match="aware"):
        itinerary.current(AT.replace(tzinfo=None))


def test_illness_replan_preserves_meals_and_real_return_travel(itinerary):
    itinerary.ensure(AT, *windows(AT))
    at = AT.replace(hour=14, minute=30)
    before = tuple(item for item in itinerary.day(at) if item.ends_at <= at)
    assert itinerary.adapt(at, needs={"ill": True}, cause_id="illness-test")
    revised = itinerary.day(at)
    assert tuple(item for item in revised if item.ends_at < at) == before
    road = itinerary.current(at)
    assert road.kind == "travel" and road.origin == "универ"
    assert road.ends_at - at == timedelta(minutes=35)
    assert itinerary.current(road.ends_at).location == "дом"
    assert {item.kind for item in revised if item.starts_at >= at} >= {
        "lunch",
        "dinner",
        "rest",
    }
    assert not any(item.can_study for item in revised if item.starts_at >= at)


def test_rain_ends_walk_with_a_persisted_walking_return(itinerary):
    day = AT + timedelta(days=1)
    itinerary.config["itinerary"]["outing_chance"] = 1.0
    plan = itinerary.ensure(day, *windows(day), needs={"cash": 500})
    walk = next(item for item in plan if item.kind == "walk")
    at = walk.starts_at + timedelta(minutes=10)
    assert itinerary.adapt(at, needs={"rain": True}, cause_id="observed-rain")
    road = itinerary.current(at)
    assert road.origin == "парк" and road.destination == "дом"
    assert road.label == itinerary.config["itinerary"]["labels"]["walk_travel"]
    assert road.ends_at - at == timedelta(minutes=20)
    assert itinerary.current(road.ends_at).location == "дом"
    saved = itinerary.day(day)
    itinerary.adapt(at, needs={"rain": True}, cause_id="observed-rain")
    assert itinerary.day(day) == saved


def test_planning_tomorrows_sleep_does_not_apply_future_debt(tmp_path):
    from src.providers import RuntimeProviders

    database = Database(tmp_path / "sleep.sqlite3")
    database.initialize()
    provider = RuntimeProviders(database, Path("config"), clock=lambda: AT)
    planned = provider.sleep.plan((AT + timedelta(days=1)).date(), at=AT)
    assert planned.wake > AT
    with database.connection(readonly=True) as connection:
        row = connection.execute(
            "SELECT * FROM sleep_log WHERE night=?", (planned.wake.date(),)
        ).fetchone()
        assert row["debt_applied"] == 0


def test_future_revision_preserves_past_and_cannot_teleport(itinerary):
    from src.core.itinerary import Activity

    original = itinerary.ensure(AT, *windows(AT))
    at = AT.replace(hour=20)
    replacement = Activity(
        "rest:illness",
        AT.date().isoformat(),
        at,
        AT + timedelta(days=1),
        "дом",
        "rest",
        "Resting at home",
    )
    itinerary.revise(at, [replacement], cause_id="illness")
    assert itinerary.current(at).id == "rest:illness"
    assert not itinerary.current(at).can_study
    for item in original:
        if item.ends_at <= at:
            assert itinerary.current(item.starts_at) == item
    itinerary.revise(at, [replacement], cause_id="illness")
    with pytest.raises(ValueError, match="location"):
        itinerary.revise(
            at, [replace(replacement, id="teleport", location="парк")], cause_id="bad"
        )


async def test_runtime_context_reads_persisted_activity_and_actual_subject(tmp_path):
    from src.providers import RuntimeProviders

    database = Database(tmp_path / "runtime.sqlite3")
    database.initialize()
    runtime = RuntimeProviders(database, Path("config"), clock=lambda: AT)
    try:
        at = AT.replace(hour=14, minute=30)
        blocks = await runtime.context(at)
        day = blocks["day"]
        activity = runtime.itinerary.current(at)
        assert day.activity_id == activity.id
        assert day.subject == "практикум"
        assert day.location == "универ"
        assert day.busy and day.chat_allowed and not day.study_allowed
        assert not day.blackout.blocked
        sleep = runtime.sleep.current(at)[0]
        assert runtime.world.world.where(at, sleep=sleep) == day.location
    finally:
        await runtime.close()
