"""Run a controlled Almaty-calendar simulation without models or publication."""

import argparse
import csv
import json
import logging
from collections import defaultdict
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
from src.core.time_utils import ALMATY, add_elapsed, require_aware, to_utc_iso


def local_clock(day: datetime, text: str) -> datetime:
    day = require_aware(day)
    hour, minute = map(int, text.split(":"))
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def simulate(
    *, start: datetime, days: int, seed: int, output: Path, config: Path, scenario: dict
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

    def context_at(at):
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
    for index in range(days):
        day = start + timedelta(days=index)
        for event in scenario["daily"]:
            actions[local_clock(day, event["time"])].append(("event", event["event"]))
        rotation = scenario["study_rotation"]
        actions[local_clock(day, scenario["study_time"])].append(
            ("event", rotation[index % len(rotation)])
        )
    for trigger in scenario["triggers"]:
        if trigger["day"] < days:
            at = local_clock(start + timedelta(days=trigger["day"]), trigger["time"])
            actions[at].append(("trigger", trigger["id"]))

    samples, transitions = [], []
    for at in sorted(samples_at | actions.keys(), key=lambda item: item.timestamp()):
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
        )
    except Exception:
        structlog.get_logger("blogai.simulation").exception("simulation_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
