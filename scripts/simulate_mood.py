"""Run a controlled Almaty-calendar simulation without models or publication."""

import argparse
import csv
import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from random import Random
from statistics import fmean

import structlog

from src.core.db import Database
from src.core.logging import configure_logging
from src.core.mood import BaselineContext, MoodModel, MoodService
from src.core.pad import AXES
from src.core.schedule import Schedule, SleepWindow
from src.core.time_utils import (
    ALMATY,
    add_elapsed,
    local_clock,
    require_aware,
    to_utc_iso,
)
from src.core.world import World


def simulate_nights(start, days, schedule, scenario, rng):
    """Use explicit fixture facts where the production specification is incomplete."""
    nights = []
    debt = scenario["initial_debt"]
    for index in range(days):
        day = start + timedelta(days=index)
        complexities, labels = scenario["complexity_rotation"], scenario["sleep_labels"]
        bedtime = schedule.plan_bedtime(
            day - timedelta(days=1),
            last_complexity=complexities[index % len(complexities)],
            mood=labels[index % len(labels)],
            rng=rng,
        )
        wake = schedule.resolve_wake(
            day,
            rng=rng,
            trigger_states=scenario["trigger_states"],
            interruption_times={
                key: local_clock(day, value)
                for key, value in scenario["interruption_times"].items()
            },
        )
        sleep = SleepWindow(bedtime, wake.at)
        debt = schedule.sleep_debt(debt, sleep)
        nights.append((bedtime, wake, sleep, debt))
    return nights


def simulate(
    *,
    start: datetime,
    days: int,
    seed: int,
    output: Path,
    config: Path,
    scenario: dict,
    with_schedule: bool = False,
) -> dict:
    start = require_aware(start)
    if days <= 0:
        raise ValueError("Simulation duration must be positive")
    database_path = output / "state.sqlite3"
    if database_path.exists():
        raise FileExistsError(
            "Use a fresh output directory; existing history is preserved"
        )
    output.mkdir(parents=True, exist_ok=True)
    configure_logging(output / "events.jsonl", level=logging.WARNING)
    model = MoodModel.from_config(config, epoch=start)
    database = Database(database_path)
    database.initialize()
    rng = Random(seed)
    schedule = Schedule.from_config(config) if with_schedule else None
    world = World.from_config(config) if with_schedule else None
    calendar_rng = Random(seed)
    nights = (
        simulate_nights(start, days, schedule, scenario["schedule"], calendar_rng)
        if with_schedule
        else []
    )

    def context_at(at):
        if with_schedule:
            completed = [
                night for night in nights if night[2].wake.timestamp() <= at.timestamp()
            ]
            debt = (
                completed[-1][3] if completed else scenario["schedule"]["initial_debt"]
            )
            return BaselineContext(sleep_debt=debt)
        index = (at.date() - start.date()).days
        debts = scenario["sleep_debt_rotation"]
        return BaselineContext(sleep_debt=debts[index % len(debts)])

    initial = model.baseline(start, context_at(start))
    service = MoodService(database, model, initial=initial, initial_at=start)
    actions = defaultdict(list)
    samples_at = set()
    end = start + timedelta(days=days)
    at = start
    while at.timestamp() < end.timestamp():
        samples_at.add(at)
        at = add_elapsed(at, hours=1)
    world_at = set(samples_at)
    for index in range(days):
        day = start + timedelta(days=index)
        if with_schedule:
            world_at.update(
                local_clock(day, clock)
                for clock in scenario["schedule"]["blackout_checks"]
            )
        daily = scenario["schedule"]["daily"] if with_schedule else scenario["daily"]
        for event in daily:
            actions[local_clock(day, event["time"])].append(("event", event["event"]))
        rotation = scenario["study_rotation"]
        actions[local_clock(day, scenario["study_time"])].append(
            ("event", rotation[index % len(rotation)])
        )
    for trigger in scenario["triggers"]:
        if trigger["day"] < days:
            at = local_clock(start + timedelta(days=trigger["day"]), trigger["time"])
            actions[at].append(("trigger", trigger["id"]))
    for _, wake, _, _ in nights:
        if wake.event_id is not None:
            actions[wake.at].append(("event", wake.event_id))

    samples, transitions, world_rows = [], [], []
    for at in sorted(world_at | actions.keys(), key=lambda item: item.timestamp()):
        for resolution in service.pending_resolutions(at):
            state = service.record_event(
                resolution.event_id,
                at=resolution.fire_at,
                context=context_at(resolution.fire_at),
                queue_id=resolution.id,
            )
            transitions.append(state)
        for kind, event_id in actions.get(at, []):
            if kind == "trigger":
                week = (at.date() - start.date()).days // 7
                state = service.fire_trigger(
                    event_id,
                    at=at,
                    context=context_at(at),
                    week_start=start + timedelta(days=7 * week),
                    next_exam_at=None,
                    rng=rng,
                )
            else:
                state = service.record_event(event_id, at=at, context=context_at(at))
            transitions.append(state)
        if at in samples_at:
            samples.append(service.view(at, context_at(at)))
        if with_schedule and at in world_at:
            sleeping = [night for night in nights if night[2].contains(at)]
            completed = [
                night for night in nights if night[2].wake.timestamp() <= at.timestamp()
            ]
            night = (sleeping or completed or nights[:1])[-1]
            sleep = night[2]
            activity = (
                "sleep"
                if sleep.contains(at)
                else "class"
                if any(lesson.contains(at) for lesson in schedule.classes(at))
                else "commute"
                if schedule.in_commute(at)
                else "free"
            )
            day_context = world.day_context(
                at,
                sleep=sleep,
                sleep_debt=context_at(at).sleep_debt,
                location=scenario["schedule"]["locations"][activity],
                road_roll=calendar_rng.random(),
            )
            world_rows.append(
                {
                    "at": to_utc_iso(at),
                    "bedtime": to_utc_iso(day_context.bedtime),
                    "wake_time": to_utc_iso(day_context.wake_time),
                    "sleep_debt": round(day_context.sleep_debt, 4),
                    "location": day_context.location,
                    "daypart": day_context.daypart,
                    "blackout": day_context.blackout.reason or "allowed",
                }
            )

    longest, extreme_counts = {}, {}
    for axis in AXES:
        run = maximum = total = 0
        for state in samples:
            extreme = abs(getattr(state.mood, axis)) == 1
            run = run + 1 if extreme else 0
            maximum = max(maximum, run)
            total += extreme
        longest[axis], extreme_counts[axis] = maximum, total
    report = {
        "timezone": "Asia/Almaty",
        "start": start.isoformat(),
        "days": days,
        "seed": seed,
        "samples": len(samples),
        "events": len(transitions),
        "mean": {
            axis: round(fmean(getattr(state.mood, axis) for state in samples), 4)
            for axis in AXES
        },
        "minimum": {
            axis: min(getattr(state.mood, axis) for state in samples) for axis in AXES
        },
        "maximum": {
            axis: max(getattr(state.mood, axis) for state in samples) for axis in AXES
        },
        "extreme_samples": extreme_counts,
        "longest_extreme_run": longest,
        "scenario": scenario["description"],
    }
    if with_schedule:
        report["sleep_nights"] = len(nights)
        report["blackout_checks"] = len(world_rows)
        report["blackout_samples"] = dict(
            Counter(row["blackout"] for row in world_rows)
        )
        report["final_sleep_debt"] = round(nights[-1][3], 4)
        sleep_rows = [
            {
                "bedtime": to_utc_iso(sleep.bedtime),
                "planned_bedtime": to_utc_iso(bedtime),
                "actual_wake": to_utc_iso(sleep.wake),
                "reason": wake.reason,
                "hours": round(sleep.hours, 4),
                "debt": round(debt, 4),
            }
            for bedtime, wake, sleep, debt in nights
        ]
        for name, rows in (("sleep", sleep_rows), ("world", world_rows)):
            with (output / f"{name}.csv").open(
                "w", encoding="utf-8", newline=""
            ) as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    (output / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    for name, states in (("samples", samples), ("transitions", transitions)):
        with (output / f"{name}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=["at", "P", "A", "D", "sleep_debt", "last_event"]
            )
            writer.writeheader()
            for state in states:
                writer.writerow(
                    {
                        "at": to_utc_iso(state.at),
                        **asdict(state.mood),
                        "sleep_debt": state.sleep_debt,
                        "last_event": state.last_event,
                    }
                )
    print("Date         Mean P   Mean A   Mean D   Events")
    for index in range(days):
        day = (start + timedelta(days=index)).date()
        values = [state for state in samples if state.at.date() == day]
        means = [fmean(getattr(state.mood, axis) for state in values) for axis in AXES]
        count = sum(state.at.date() == day for state in transitions)
        print(
            f"{day.isoformat()}  {means[0]:7.4f}  {means[1]:7.4f} "
            f" {means[2]:7.4f}  {count:7d}"
        )
    if with_schedule:
        print("Date         Bedtime   Wake      Hours   Debt   Wake reason")
        for _, wake, sleep, debt in nights:
            print(
                f"{wake.at.date()}   {sleep.bedtime:%H:%M}     {wake.at:%H:%M}"
                f"    {sleep.hours:5.2f}  {debt:5.2f}   {wake.reason}"
            )
    print("Local event time           P        A        D  Event")
    for state in transitions:
        print(
            f"{state.at:%Y-%m-%d %H:%M:%S}  {state.mood.P:7.4f}"
            f"  {state.mood.A:7.4f}  {state.mood.D:7.4f}  {state.last_event}"
        )
    print(json.dumps(report, sort_keys=True))
    structlog.get_logger("blogai.simulation").info(
        "simulation_completed", report=report
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--with-schedule", action="store_true")
    parser.add_argument(
        "--scenario",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "tests/fixtures/mood_scenario.json",
    )
    args = parser.parse_args()
    start = datetime(args.start.year, args.start.month, args.start.day, tzinfo=ALMATY)
    try:
        simulate(
            start=start,
            days=args.days,
            seed=args.seed,
            output=args.output,
            config=args.config,
            scenario=json.loads(args.scenario.read_text(encoding="utf-8")),
            with_schedule=args.with_schedule,
        )
    except Exception:
        structlog.get_logger("blogai.simulation").exception("simulation_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
