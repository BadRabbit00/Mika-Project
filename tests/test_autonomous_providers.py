"""Autonomous calendar facts, expiring overrides, and durable sleep accounting."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from random import Random
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from src.core.db import Database
from src.core.pad import Mood
from src.core.schedule import SleepWindow, WakeEvent
from src.core.settings import SettingsRegistry, SQLiteSettingsStore
from src.core.time_utils import ALMATY, to_utc_iso
from src.core.world import World
from src.providers import DerivedWorldProvider, LiveInputs, RuntimeProviders

AT = datetime(2026, 9, 21, 16, tzinfo=ALMATY)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "world.sqlite3")
    database.initialize()
    return database


def providers(db, path=None, *, at=AT):
    settings = SettingsRegistry.from_file(
        Path("config/settings.yaml"), store=SQLiteSettingsStore(db)
    )
    return RuntimeProviders(db, Path("config"), path, settings, clock=lambda: at)


def snapshot(**overrides):
    return (
        dict(
            initial_mood=dict(P=0, A=0, D=0),
            initial_mood_at=to_utc_iso(AT),
            initial_sleep_debt=0,
        )
        | overrides
    )


def rows(db):
    with db.connection() as c:
        return [
            dict(row) for row in c.execute("SELECT * FROM sleep_log ORDER BY wake_at")
        ]


async def test_autonomous_start_without_file_is_neutral_and_restarts(db, monkeypatch):
    from src import providers as provider_module

    recorded = Mock()
    monkeypatch.setattr(provider_module.log, "info", recorded)
    first = providers(db)
    assert recorded.call_args.kwargs["source"] == "neutral_defaults"
    try:
        context = await first.context(AT)
        assert context["mood"] == Mood(0, 0, 0)
        assert context["day"].sleep_debt == 0
        assert context["day"].at.tzinfo == ALMATY
        future = await first.context(AT + timedelta(days=3))
        before = rows(db)
    finally:
        await first.close()
    restarted = providers(db, at=AT + timedelta(days=3))
    try:
        assert await restarted.context(AT + timedelta(days=3)) == future
        assert rows(db) == before
    finally:
        await restarted.close()


async def test_sleep_stuck_label_and_latest_complexity_come_from_history(
    db, monkeypatch
):
    before = AT.replace(hour=0) - timedelta(hours=1)

    def seed(c):
        for index in range(2):
            c.execute(
                "INSERT INTO learning_events(id,trace_id,at,event_json,state_json) "
                "VALUES (?,?,?,?,?)",
                (
                    str(index),
                    "fixture",
                    before,
                    json.dumps(dict(kind="quiz_done", total=5, answered=0)),
                    json.dumps(
                        dict(phase="WAITING", topic="security", quiz_threshold=0.6)
                    ),
                ),
            )
        c.execute(
            "INSERT INTO sources(id,path,title,ingested_at,complexity) "
            "VALUES ('hard','fixture','Fixture',?,9)",
            (before,),
        )

    db.run_transaction(seed)
    runtime = providers(db)
    schedule = runtime.sleep.history.schedule
    spy = Mock(wraps=schedule.plan_bedtime)
    monkeypatch.setattr(schedule, "plan_bedtime", spy)
    try:
        await runtime.context(AT)
        assert spy.call_args.kwargs["mood"] == "stuck"
        assert spy.call_args.kwargs["last_complexity"] == 9
    finally:
        await runtime.close()


async def test_legacy_sleep_keys_are_adopted_without_charging_debt_twice(db):
    from src.core.schedule import Schedule
    from src.core.sleep import SleepHistory

    history = SleepHistory(db, Schedule.from_config(Path("config")), initial_debt=0)
    sleep = SleepWindow(AT.replace(hour=23) - timedelta(days=1), AT.replace(hour=6))
    history.record(sleep, planned_bedtime=sleep.bedtime, reason="observed")
    assert history.complete(sleep.bedtime.date(), at=AT) == 1
    runtime = providers(db)
    try:
        context = await runtime.context(AT)
        assert context["day"].sleep_debt == 1
        row = rows(db)[0]
        assert row["night"] == AT.date().isoformat()
        assert row["debt_after"] == 1 and row["debt_applied"] == 1
    finally:
        await runtime.close()


def test_cli_allows_live_run_without_world_state(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from src import cli
    from src.catalogue import Catalogue

    monkeypatch.setattr(Catalogue, "load", Mock())
    run = AsyncMock()
    monkeypatch.setattr(cli, "run_telegram", run)
    assert (
        cli.main(
            [
                "run",
                "--layout",
                str(tmp_path / "layout.yaml"),
                "--log-file",
                str(tmp_path / "run.jsonl"),
            ]
        )
        == 0
    )
    assert run.call_args.args[0].world_state is None
    run.assert_awaited_once()


def test_initial_snapshot_and_override_validation():
    initial = LiveInputs.model_validate(snapshot())
    assert not initial.active(AT)
    with pytest.raises(ValidationError, match="valid_until"):
        LiveInputs.model_validate(snapshot(location="дом"))
    with pytest.raises(ValidationError):
        LiveInputs.model_validate(snapshot(initial_mood_at="2026-09-21T16:00:00"))
    override = LiveInputs.model_validate(
        snapshot(location="улица", valid_until=to_utc_iso(AT + timedelta(hours=1)))
    )
    assert override.active(AT)
    assert not override.active(AT + timedelta(hours=1))


@pytest.mark.parametrize("hour", [3, 10, 14, 18, 20])
def test_where_uses_architecture_probabilities_and_local_calendar(hour):
    world = World.from_config(Path("config"))
    at = AT.replace(hour=hour)
    sleep = SleepWindow(AT.replace(hour=1), AT.replace(hour=8))
    rng = Random(at.date().isoformat())
    if sleep.contains(at):
        expected = "дом"
    elif 9 <= hour < 14:
        expected = rng.choices(["универ", "транспорт"], weights=[0.85, 0.15])[0]
    elif 14 <= hour < 19:
        expected = rng.choices(["дом", "кофейня", "улица"], weights=[0.6, 0.25, 0.15])[
            0
        ]
    else:
        expected = "дом"
    assert world.where(at, sleep=sleep) == expected
    assert world.where(at, sleep=sleep) == expected


async def test_expired_location_override_returns_to_derived_world(db, tmp_path):
    path = tmp_path / "state.json"
    until = AT + timedelta(hours=1)
    path.write_text(
        json.dumps(
            snapshot(location="транспорт", road_roll=0, valid_until=to_utc_iso(until))
        )
    )
    runtime = providers(db, path)
    assert isinstance(runtime.world, DerivedWorldProvider)
    try:
        assert (await runtime.context(AT))["day"].location == "транспорт"
        automatic = await runtime.context(until)
        sleep, _, _ = runtime.sleep.current(until)
        assert automatic["day"].location == runtime.world.world.where(
            until, sleep=sleep
        )
        assert not automatic["day"].blackout.blocked
    finally:
        await runtime.close()


async def test_sleep_plans_use_existing_formulas_and_debt_once(db, monkeypatch):
    runtime = providers(db)
    schedule = runtime.sleep.history.schedule
    bedtime = Mock(wraps=schedule.plan_bedtime)
    wake = Mock(wraps=schedule.resolve_wake)
    monkeypatch.setattr(schedule, "plan_bedtime", bedtime)
    monkeypatch.setattr(schedule, "resolve_wake", wake)
    try:
        await runtime.context(AT)
        for days in range(1, 15):
            at = AT + timedelta(days=days)
            context = await runtime.context(at)
            debt = 0
            for row in rows(db):
                if row["debt_applied"] and row["wake_at"] > to_utc_iso(AT):
                    hours = (
                        datetime.fromisoformat(row["wake_at"])
                        - datetime.fromisoformat(row["actual_bedtime"])
                    ).total_seconds() / 3600
                    debt = round(min(12, max(0, debt + (8 - hours))), 4)
            assert context["day"].sleep_debt == debt
            before = rows(db)
            assert await runtime.context(at) == context
            assert rows(db) == before
        assert bedtime.call_count >= 14 and wake.call_count == bedtime.call_count
    finally:
        await runtime.close()


async def test_sleep_mood_label_uses_schedule_rules(db, tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(snapshot(initial_mood=dict(P=-0.5, A=0, D=0))))
    runtime = providers(db, path)
    schedule = runtime.sleep.history.schedule
    spy = Mock(wraps=schedule.plan_bedtime)
    monkeypatch.setattr(schedule, "plan_bedtime", spy)
    try:
        await runtime.context(AT)
        assert any(call.kwargs["mood"] == "down" for call in spy.call_args_list)
    finally:
        await runtime.close()


async def test_sleep_night_keys_handle_bedtime_before_midnight(db, monkeypatch):
    runtime = providers(db)
    schedule = runtime.sleep.history.schedule
    monkeypatch.setattr(
        schedule, "plan_bedtime", lambda day, **kw: day.replace(hour=23, minute=30)
    )
    monkeypatch.setattr(
        schedule,
        "resolve_wake",
        lambda day, **kw: WakeEvent("free", day.replace(hour=9, minute=0), None),
    )
    try:
        for days in range(3):
            await runtime.context(AT + timedelta(days=days))
        nights = rows(db)
        assert len({row["night"] for row in nights}) == len(nights)
        assert all(row["hours"] == 9.5 for row in nights)
        night = await runtime.context(
            AT.replace(hour=23, minute=45) + timedelta(days=2)
        )
        assert night["day"].blackout.reason == "sleep"
    finally:
        await runtime.close()


async def test_expired_future_sleep_override_is_replaced_by_schedule(db, tmp_path):
    path = tmp_path / "state.json"
    tomorrow = AT + timedelta(days=1)
    path.write_text(
        json.dumps(
            snapshot(
                valid_until=to_utc_iso(AT + timedelta(hours=1)),
                sleep=[
                    dict(
                        bedtime=to_utc_iso(tomorrow.replace(hour=1)),
                        planned_bedtime=to_utc_iso(tomorrow.replace(hour=1)),
                        wake=to_utc_iso(tomorrow.replace(hour=18)),
                        reason="operator",
                    )
                ],
            )
        )
    )
    runtime = providers(db, path)
    try:
        await runtime.context(AT)
        context = await runtime.context(tomorrow)
        assert context["wake_reason"] != "operator"
        assert all(row["wake_reason"] != "operator" for row in rows(db))
    finally:
        await runtime.close()


async def test_expired_distant_override_does_not_skip_intervening_nights(db, tmp_path):
    path = tmp_path / "state.json"
    distant = AT + timedelta(days=10)
    path.write_text(
        json.dumps(
            snapshot(
                valid_until=to_utc_iso(AT + timedelta(hours=1)),
                sleep=[
                    dict(
                        bedtime=to_utc_iso(distant.replace(hour=1)),
                        planned_bedtime=to_utc_iso(distant.replace(hour=1)),
                        wake=to_utc_iso(distant.replace(hour=18)),
                        reason="operator",
                    )
                ],
            )
        )
    )
    runtime = providers(db, path)
    try:
        await runtime.context(AT)
        await runtime.context(AT + timedelta(hours=2))
        await runtime.context(AT + timedelta(days=3))
        recorded = {row["night"] for row in rows(db)}
        assert all(
            (AT + timedelta(days=i)).date().isoformat() in recorded for i in range(4)
        )
    finally:
        await runtime.close()


async def test_early_bedtime_is_blocked_before_the_night_activity_window(
    db, monkeypatch
):
    runtime = providers(db)
    schedule = runtime.sleep.history.schedule
    monkeypatch.setattr(
        schedule, "plan_bedtime", lambda day, **kw: day.replace(hour=21, minute=0)
    )
    monkeypatch.setattr(
        schedule,
        "resolve_wake",
        lambda day, **kw: WakeEvent("free", day.replace(hour=9, minute=0), None),
    )
    try:
        assert (await runtime.context(AT.replace(hour=21, minute=30)))[
            "day"
        ].blackout.reason == "sleep"
    finally:
        await runtime.close()
