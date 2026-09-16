"""Literal mood contracts; schedule contracts follow the verified mood stage."""

import json
import math
import sqlite3
import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from pathlib import Path
from random import Random

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
