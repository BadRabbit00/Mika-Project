"""Literal mood, biological time, and publication blackout contracts."""

import json
import math
import sqlite3
import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from pathlib import Path
from random import Random
from unittest.mock import Mock

import pytest

from src.core.db import Database, insert_mood
from src.core.mood import (
    BaselineContext,
    Mood,
    MoodModel,
    MoodService,
    PendingResolution,
    TriggerBlocked,
    UnspecifiedResolution,
    apply,
    decay,
)
from src.core.time_utils import ALMATY, add_elapsed, elapsed_hours
from src.core.schedule import Schedule, ScheduleGap, SleepWindow
from src.core.world import World

START = datetime(2026, 9, 14, tzinfo=ALMATY)


@pytest.fixture
def model():
    return MoodModel.from_config(Path("config"), epoch=START)


@pytest.fixture
def service(tmp_path, model):
    database = Database(tmp_path / "mood.sqlite3")
    database.initialize()
    return MoodService(database, model, initial=Mood(0, 0, 0), initial_at=START)


def test_inertia_outward():
    assert apply("D", 0.9, +0.3) - 0.9 < apply("D", 0.0, +0.3) - 0.0


def test_inertia_inward():
    assert abs(apply("P", -0.8, +0.3) + 0.8) > abs(apply("P", 0.0, +0.3))


def test_pierce():
    assert apply("P", 0.95, -0.55, pierce=True) < 0.6
    assert apply("P", -0.95, -0.2, pierce=True) < apply("P", -0.95, -0.2)


def test_decay_to_baseline():
    # TODO(DECAY-ASSERTION): the section 31.1 six-hour <0.1 claim is inconsistent.
    assert decay("A", 0.9, baseline=-0.4, hours=6) == pytest.approx(-0.2375)
    assert abs(decay("A", 0.9, baseline=-0.4, hours=6) + 0.4) == pytest.approx(0.1625)
    assert abs(decay("A", 0.9, baseline=-0.4, hours=8) + 0.4) < 0.1


def test_clamp():
    assert apply("P", 0.99, +5.0) == 1.0
    assert apply("P", -0.99, -500.0, pierce=True) == -1.0


def test_mood_inertia(model):
    for axis in ("P", "A", "D"):
        c = model.base_coefficients[axis]
        for cur in (-0.99, -0.4, 0.0, 0.7, 0.99):
            for delta in (-0.55, 0.0, 0.35):
                outward = cur == 0 or (delta > 0) - (delta < 0) == (cur > 0) - (cur < 0)
                resistance = (
                    (1 - abs(cur)) ** (c.out_pos if delta > 0 else c.out_neg)
                    if outward
                    else 1 + abs(cur) * c.inward_boost
                )
                expected = max(-1.0, min(1.0, cur + delta * resistance))
                assert apply(axis, cur, delta) == expected


def test_mood_is_immutable_and_bounded():
    value = Mood(0.1, -0.2, 0.3)
    with pytest.raises(FrozenInstanceError):
        value.P = 0.5
    for invalid in (1.1, -1.1, math.nan, math.inf, True):
        with pytest.raises(ValueError):
            Mood(invalid, 0, 0)


def test_cycle_modifiers_are_literal_and_repeat_after_28_days(model):
    first = model.cycle.at(START)
    assert first.day == 12 and first.id == "follicular"
    assert model.coefficients(START)["D"].out_pos == 2.0 * 0.85
    late_at = START + timedelta(days=12)
    late = model.cycle.at(late_at)
    assert late.day == 24 and late.id == "luteal"
    assert late.baseline.P == -0.15 + -0.10
    assert model.coefficients(late_at)["P"].out_neg == 1.4 * 0.8
    assert model.coefficients(late_at)["P"].inward_boost == 0.9 * 0.7
    assert model.cycle.at(START + timedelta(days=28)) == first


def test_floating_baseline_uses_dayparts_debt_and_explicit_history(model):
    morning = START.replace(hour=8)
    baseline = model.baseline(morning, BaselineContext(sleep_debt=4))
    assert baseline.P == pytest.approx(0.15 + 4 * -0.05 + 0.10)
    assert baseline.A == -0.6
    failed = model.baseline(
        morning, BaselineContext(exam_result="failed", stuck_days=2)
    )
    assert failed.P == pytest.approx(0.15 - 0.20 - 2 * 0.07 + 0.10)
    assert failed.D == pytest.approx(-0.30 + 0.05)
    assert model.daypart(START.replace(hour=23)) == "night"
    assert model.daypart(START.replace(hour=2)) == "deep_night"


def test_mood_block_has_no_numbers_or_cycle_details(model):
    for value in (-1, -0.66, -0.33, -0.15, 0, 0.33, 0.66, 1):
        block = model.mood_block(Mood(value, value, value))
        assert not any(character.isdigit() for character in block)
        assert all(
            phase not in block
            for phase in ("menstrual", "follicular", "ovulatory", "luteal")
        )
        assert len(block.splitlines()) == 5
    assert model.octant(Mood(-0.15, -0.15, -0.15)) == "+++"
    assert model.band_for("P", 0)["id"] == "P+1"
    assert model.band_for("P", 1)["id"] == "P+3"


def test_lazy_decay_is_read_only_and_does_not_depend_on_reads(service):
    context = BaselineContext()
    service.record_event("exam_failed", at=START + timedelta(hours=1), context=context)
    expected = service.view(START + timedelta(hours=7), context).mood
    service.view(START + timedelta(hours=3), context)
    assert service.view(START + timedelta(hours=7), context).mood == expected
    with service.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM mood").fetchone()[0] == 1


def test_all_saved_mood_floats_are_rounded(service):
    state = service.record_event(
        "slept_badly",
        at=START + timedelta(hours=1),
        context=BaselineContext(sleep_debt=1.234567),
    )
    with service.database.connection() as connection:
        row = connection.execute("SELECT * FROM mood").fetchone()
        assert row["at"].endswith("Z")
        for key in (
            "p",
            "a",
            "d",
            "baseline_p",
            "baseline_a",
            "baseline_d",
            "sleep_debt",
        ):
            assert row[key] == round(row[key], 4)
    assert state.sleep_debt == 1.2346


def test_sleep_debt_storage_rejects_unrounded_direct_writes(service):
    with service.database.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO mood(at, sleep_debt) VALUES (?, ?)", (START, 1.234567)
            )
    with pytest.raises(ValueError):
        service.database.run_transaction(
            lambda connection: insert_mood(
                connection,
                at=START,
                p=0,
                a=0,
                d=0,
                baseline_p=0,
                baseline_a=0,
                baseline_d=0,
                sleep_debt=math.inf,
            )
        )


def test_trigger_resolution_is_atomic_and_consumed_once(service):
    at = START + timedelta(hours=18)
    service.fire_trigger(
        "fight_with_boyfriend",
        at=at,
        context=BaselineContext(),
        week_start=START,
        next_exam_at=None,
        rng=Random(1),
    )
    queue = service.pending_resolutions(START + timedelta(days=3))
    assert len(queue) == 1 and queue[0].event_id == "made_up"
    assert queue[0].fire_at == add_elapsed(at, hours=20)
    with pytest.raises(PendingResolution):
        service.view(queue[0].fire_at, BaselineContext())
    service.record_event(
        queue[0].event_id,
        at=queue[0].fire_at,
        context=BaselineContext(),
        queue_id=queue[0].id,
    )
    assert service.pending_resolutions(START + timedelta(days=3)) == []
    with pytest.raises(LookupError):
        service.record_event(
            queue[0].event_id,
            at=queue[0].fire_at,
            context=BaselineContext(),
            queue_id=queue[0].id,
        )


def test_trigger_guards_and_unknown_events_do_not_write(service):
    context = BaselineContext()
    at = START + timedelta(hours=1)
    with pytest.raises(TriggerBlocked):
        service.fire_trigger(
            "fight_with_boyfriend",
            at=at,
            context=context,
            week_start=START,
            next_exam_at=at + timedelta(hours=11),
            rng=Random(1),
        )
    with pytest.raises(KeyError):
        service.record_event("invented_event", at=at, context=context)
    service.fire_trigger(
        "boyfriend_sweet",
        at=at,
        context=context,
        week_start=START,
        next_exam_at=None,
        rng=Random(1),
    )
    with pytest.raises(TriggerBlocked):
        service.fire_trigger(
            "parents_proud",
            at=at + timedelta(days=5),
            context=context,
            week_start=START,
            next_exam_at=None,
            rng=Random(1),
        )
    with service.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM mood").fetchone()[0] == 1


def test_repeated_timestamp_is_rejected_without_rewriting_history(service):
    at = START + timedelta(hours=1)
    service.record_event("article_easy", at=at, context=BaselineContext())
    with pytest.raises(ValueError, match="chronological"):
        service.record_event("article_hard", at=at, context=BaselineContext())


def test_unspecified_trigger_outcome_does_not_mutate_state(service):
    with pytest.raises(UnspecifiedResolution):
        service.fire_trigger(
            "parents_pressure",
            at=START,
            context=BaselineContext(),
            week_start=START,
            next_exam_at=None,
            rng=Random(2),
        )
    with service.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM mood").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM mood_queue").fetchone()[0] == 0


@pytest.mark.parametrize(
    "offset, phase",
    [(2, "ovulatory"), (5, "luteal"), (17, "menstrual"), (22, "follicular")],
)
def test_cycle_phase_boundaries(model, offset, phase):
    assert model.cycle.at(START + timedelta(days=offset)).id == phase


def test_mood_time_is_aware_and_uses_elapsed_hours(service, model):
    naive = START.replace(tzinfo=None)
    for call in (
        lambda: service.view(naive, BaselineContext()),
        lambda: model.cycle.at(naive),
    ):
        with pytest.raises(ValueError, match="aware"):
            call()
    before = datetime(2024, 2, 29, 23, 30, tzinfo=ALMATY, fold=0)
    after = add_elapsed(before, hours=1)
    assert after.hour == 23 and after.fold == 1 and after.tzinfo == ALMATY
    assert elapsed_hours(before, after) == 1


def test_two_week_mood_simulation(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/simulate_mood.py",
            "--start",
            "2026-09-14",
            "--days",
            "14",
            "--seed",
            "1",
            "--output",
            str(tmp_path / "simulation"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "simulation/report.json").read_text())
    assert report["days"] == 14 and report["samples"] == 14 * 24
    assert report["timezone"] == "Asia/Almaty"
    assert all(-1 <= value <= 1 for value in report["mean"].values())
    assert all(value == 0 for value in report["longest_extreme_run"].values())
    assert (tmp_path / "simulation/transitions.csv").is_file()
    assert "Mean P" in result.stdout


@pytest.fixture
def schedule():
    return Schedule.from_config(Path("config"))


def wake_options(day=START):
    return {
        "trigger_states": {"not_fighting": False},
        "interruption_times": {
            "dasha_hairdryer": day.replace(hour=8),
            "delivery_doorbell": day.replace(hour=10, minute=15),
            "overslept": day.replace(hour=12, minute=20),
        },
    }


def test_sleep_plan_preserves_literal_complexity_and_mood_formula(schedule):
    rng = Mock()
    rng.gauss.side_effect = [7, -4]
    plan = schedule.plan_sleep(
        START - timedelta(days=1), last_complexity=9, mood="stuck", rng=rng
    )
    assert plan.bedtime == START.replace(hour=2, minute=19)
    assert plan.wake == START.replace(hour=7, minute=56)
    assert rng.gauss.call_args_list[0].args == (0, 35)
    assert rng.gauss.call_args_list[1].args == (0, 20)
    rng.gauss.side_effect = [0]
    rng.uniform.return_value = 8.5
    free = schedule.plan_sleep(START, last_complexity=4, mood="down", rng=rng)
    assert free.bedtime == (START + timedelta(days=1)).replace(minute=30)
    assert elapsed_hours(free.bedtime, free.wake) == 8.5
    assert rng.uniform.call_args.args == (7.5, 9.5)


def test_actual_wake_uses_first_class_and_free_windows(schedule):
    rng = Mock()
    rng.random.return_value = 0.999
    for day, hour, minute in ((START, 7, 50), (START + timedelta(days=4), 9, 40)):
        wake = schedule.wake_up(day, rng=rng, **wake_options(day))
        assert wake.reason == "alarm"
        assert wake.at == day.replace(hour=hour, minute=minute)
        assert wake.event_id == "woke_by_alarm_early"
    rng.uniform.side_effect = lambda lo, hi: (lo + hi) / 2
    for offset, hour, minute in ((1, 10, 45), (5, 11, 0)):
        day = START + timedelta(days=offset)
        wake = schedule.wake_up(day, rng=rng, **wake_options(day))
        assert wake.at == day.replace(hour=hour, minute=minute)
        assert wake.reason == "free" and wake.event_id is None


def test_wake_interruptions_stop_at_first_matching_event(schedule):
    sunday = START + timedelta(days=6)
    rng = Mock()
    rng.random.return_value = 0
    rng.uniform.side_effect = lambda lo, hi: lo
    wake = schedule.wake_up(sunday, rng=rng, **wake_options(sunday))
    assert wake.reason == "mama_call" and wake.at == sunday.replace(hour=9)
    assert wake.event_id == "wake:mama_call" and wake.hint
    assert rng.random.call_count == 1


def test_unspecified_wake_time_and_trigger_state_fail_explicitly(schedule):
    rng = Mock()
    rng.random.return_value = 0
    with pytest.raises(ScheduleGap, match="WAKE-TIMES"):
        schedule.wake_up(START, rng=rng, interruption_times={}, trigger_states={})
    rng.random.return_value = 0.999
    with pytest.raises(ScheduleGap, match="trigger state"):
        schedule.wake_up(START, rng=rng, interruption_times={}, trigger_states={})


def test_oversleep_only_matches_class_days(schedule):
    rng = Mock()
    rng.random.side_effect = [0.999, 0.999, 0]
    wake = schedule.wake_up(START, rng=rng, **wake_options())
    assert wake.reason == "overslept" and wake.missed_first_class
    assert wake.at == START.replace(hour=12, minute=20)
    tuesday = START + timedelta(days=1)
    rng.random.side_effect = [0.999, 0.999]
    rng.uniform.side_effect = lambda lo, hi: lo
    assert schedule.wake_up(tuesday, rng=rng, **wake_options(tuesday)).reason == "free"


def test_sleep_debt_uses_actual_hours_and_literal_clamp(schedule, model):
    short = SleepWindow(START.replace(hour=1), START.replace(hour=7))
    assert schedule.sleep_debt(3, short) == 5
    assert schedule.sleep_debt(11, short) == 12
    eight = SleepWindow(START.replace(hour=1), START.replace(hour=9))
    assert schedule.sleep_debt(3, eight) == 3
    long = SleepWindow(START.replace(hour=1), START.replace(hour=11))
    assert schedule.sleep_debt(3, long) == 1
    assert schedule.sleep_debt(1, long) == 0
    before = model.baseline(START.replace(hour=15), BaselineContext(sleep_debt=0))
    after = model.baseline(START.replace(hour=15), BaselineContext(sleep_debt=2))
    assert after.A == pytest.approx(before.A + 2 * -0.12)
    assert after.P == pytest.approx(before.P + 2 * -0.05)


def test_sleep_and_class_blackouts_include_boundaries_but_allow_breaks(schedule):
    sleep = SleepWindow(START.replace(hour=1), START.replace(hour=7, minute=50))
    for hour, minute, reason in ((1, 0, "sleep"), (7, 49, "sleep"),
                                 (9, 0, "class"), (10, 19, "class"),
                                 (10, 30, "class"), (12, 0, "class")):
        result = schedule.blackout(
            START.replace(hour=hour, minute=minute), sleep=sleep, road_roll=0
        )
        assert result.blocked and result.reason == reason
    for hour, minute in ((7, 50), (10, 20), (10, 25), (11, 50), (13, 20)):
        assert not schedule.blackout(
            START.replace(hour=hour, minute=minute), sleep=sleep, road_roll=0
        ).blocked


def test_road_blackout_uses_explicit_roll_and_configured_probability(schedule):
    sleep = SleepWindow(START.replace(hour=1), START.replace(hour=7))
    at = START.replace(hour=8, minute=40)
    assert not schedule.blackout(at, sleep=sleep, road_roll=0.2999).blocked
    first = schedule.blackout(at, sleep=sleep, road_roll=0.3)
    assert first.blocked and first.reason == "commute"
    assert schedule.blackout(at, sleep=sleep, road_roll=0.3) == first


def test_schedule_rejects_naive_times_and_measures_repeated_hour(schedule):
    naive = START.replace(tzinfo=None)
    for call in (
        lambda: schedule.classes(naive),
        lambda: schedule.plan_sleep(naive, last_complexity=5, mood="neutral", rng=Random(1)),
        lambda: SleepWindow(naive, START),
    ):
        with pytest.raises(ValueError, match="aware"):
            call()
    before = datetime(2024, 2, 29, 23, 30, tzinfo=ALMATY, fold=0)
    sleep = SleepWindow(before, add_elapsed(before, hours=8))
    assert sleep.hours == 8 and schedule.sleep_debt(3, sleep) == 3
    with pytest.raises(ValueError):
        SleepWindow(START, START)


def test_wake_and_class_deltas_only_change_mood_via_events(service, schedule):
    rng = Mock()
    rng.random.return_value = 0
    wake = schedule.wake_up(START, rng=rng, **wake_options())
    state = service.record_event(wake.event_id, at=wake.at, context=BaselineContext())
    assert state.last_event == "wake:dasha_hairdryer"
    lesson = schedule.classes(START)[0]
    state = service.record_event(
        lesson.event_id, at=lesson.end, context=BaselineContext()
    )
    assert state.last_event == "class:monday:0"


def test_world_context_preserves_sleep_facts_and_known_objects(schedule):
    world = World.from_config(Path("config"))
    location = next(iter(world.locations))
    sleep = SleepWindow(START.replace(hour=1), START.replace(hour=7))
    context = world.day_context(
        START.replace(hour=2), sleep=sleep, sleep_debt=2,
        location=location, road_roll=0.5
    )
    assert context.bedtime == sleep.bedtime and context.wake_time == sleep.wake
    assert context.sleep_debt == 2 and context.daypart == "deep_night"
    assert context.location == location and context.available_objects
    assert context.blackout.blocked and context.blackout.reason == "sleep"
    with pytest.raises(ValueError, match="location"):
        world.day_context(START, sleep=sleep, sleep_debt=2,
                          location="unknown", road_roll=0.5)


def test_two_week_schedule_simulation(tmp_path):
    output = tmp_path / "scheduled"
    result = subprocess.run(
        [sys.executable, "scripts/simulate_mood.py", "--start", "2026-09-14",
         "--days", "14", "--seed", "1", "--output", str(output), "--with-schedule"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["samples"] == 336 and report["sleep_nights"] == 14
    assert report["blackout_samples"]["sleep"] > 0
    assert report["blackout_samples"]["class"] > 0
    assert all(value == 0 for value in report["longest_extreme_run"].values())
    assert (output / "sleep.csv").is_file() and (output / "world.csv").is_file()
    assert "Wake reason" in result.stdout
