"""Offline multi-day acceptance run using temporary databases and factual drafts."""

import argparse
import asyncio
import json
import logging
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import structlog

from src.core.db import Database
from src.core.itinerary import Activity
from src.core.life_engine import LifeEngine
from src.core.time_utils import ALMATY
from src.core.writing_snapshot import WritingSnapshot
from src.life import LifeRuntime
from src.live import LiveApplication
from src.providers import RuntimeProviders
from src.publish import Destination, OutboxWorker, Publisher
from src.writer import WriteResult


async def simulate(days, output):
    if days < 2:
        raise ValueError("At least two days are required")
    start = datetime(2026, 9, 16, tzinfo=ALMATY)
    clock = [start]
    rows = []
    with tempfile.TemporaryDirectory(prefix="mika-life-") as directory:
        database = Database(Path(directory) / "simulation.sqlite3")
        database.initialize()
        providers = RuntimeProviders(database, Path("config"), clock=lambda: clock[0])

        async def render(event, **blocks):
            identity = event["id"]
            facts = json.loads(event["payload"])
            text = facts["facts"] or "Recorded evening retrospective"
            if facts.get("mandatory"):
                text = (
                    "; ".join(
                        f"{item['at']}: {item['kind']} ({item['reason']})"
                        for item in facts["transitions"]
                    )
                    + "; current: "
                    + facts["current"]["kind"]
                )
            snapshot = WritingSnapshot(
                "offtop",
                blocks["day"],
                blocks["mood"],
                blocks["wake_reason"],
                {"recorded_event": json.loads(event["payload"])},
                {},
            )
            database.run_transaction(
                lambda c: c.execute(
                    "INSERT INTO posts(id,kind,state,text,context_snapshot) "
                    "VALUES (?,'offtop','draft',?,?)",
                    (identity, text, snapshot.encode()),
                )
            )
            return WriteResult(identity, "draft", text, 1)

        runtime = LifeRuntime(
            providers,
            SimpleNamespace(recorded=render),
            Publisher(database),
            [Destination("diary", "mika", -100, 1, True)],
            clock=lambda: clock[0],
        )
        sent = []

        async def send(payload):
            sent.append(payload)
            return len(sent)

        async def allowed(payload, at):
            return await LiveApplication.allowed(
                SimpleNamespace(providers=providers, life=runtime), payload, at
            )

        worker = OutboxWorker(
            database,
            SimpleNamespace(send=send),
            clock=lambda: clock[0],
            allowed=allowed,
        )
        try:
            restarted = False
            while clock[0] < start + timedelta(days=days):
                before = len(sent)
                decision = await runtime.tick()
                if runtime._generation:
                    await runtime._generation
                while await worker.run_once(at=clock[0]) not in {"idle", "deferred"}:
                    pass
                activity = providers.itinerary.current(clock[0])
                if not rows or activity.id != rows[-1][0] or len(sent) > before:
                    event = sent[-1]["text"] if len(sent) > before else "—"
                    rows.append(
                        (
                            activity.id,
                            clock[0].strftime("%m-%d %H:%M"),
                            activity.location,
                            activity.label
                            + (": " + activity.subject if activity.subject else ""),
                            event,
                            "offline publication" if len(sent) > before else decision,
                        )
                    )
                if not restarted and clock[0] >= start + timedelta(days=days / 2):
                    restarted = True
                    saved_state = providers.life.state()
                    saved_day = providers.itinerary.day(clock[0])
                    receipts = len(sent)
                    await runtime.close()
                    await providers.close()
                    providers = RuntimeProviders(
                        database, Path("config"), clock=lambda: clock[0]
                    )
                    runtime = LifeRuntime(
                        providers,
                        SimpleNamespace(recorded=render),
                        Publisher(database),
                        [Destination("diary", "mika", -100, 1, True)],
                        clock=lambda: clock[0],
                    )
                    runtime.recover()
                    assert providers.life.state() == saved_state
                    assert providers.itinerary.day(clock[0]) == saved_day
                    await runtime.tick()
                    if runtime._generation:
                        await runtime._generation
                    while await worker.run_once(at=clock[0]) not in {
                        "idle",
                        "deferred",
                    }:
                        pass
                    assert len(sent) == receipts
                clock[0] = min(clock[0] + timedelta(minutes=20), activity.ends_at)
            state = providers.life.state()
            with database.connection(readonly=True) as c:
                counts = {
                    table: c.execute("SELECT count(*) FROM " + table).fetchone()[0]
                    for table in (
                        "life_days",
                        "life_events",
                        "life_tasks",
                        "money_ledger",
                        "mood",
                        "outbox",
                        "life_breaks",
                        "activity_transitions",
                    )
                }
            restart_state = providers.life.state()
            LifeEngine(database, providers.life_config, Path("config")).bootstrap(
                clock[0]
            )
            assert providers.life.state() == restart_state
            branches = []
            for mood, busy in (
                ("calm", False),
                ("tired", True),
                ("strained", False),
                ("tired", False),
            ):
                branch_db = Database(Path(directory) / (mood + str(busy) + ".sqlite3"))
                branch_db.initialize()
                config = json.loads(json.dumps(providers.life_config))
                config["money"]["initial_cash"] = 1000
                engine = LifeEngine(branch_db, config, Path("config"))
                at = start.replace(hour=19)
                engine.bootstrap(at)
                engine.observe_person("mother", at, mood=mood, busy=busy)
                engine.notice(at)
                task = next(t for t in engine.tasks() if t["kind"] == "mother_contact")
                event = engine.execute(
                    task["id"],
                    at=at,
                    activity=Activity(
                        "home",
                        str(at.date()),
                        at,
                        at + timedelta(hours=2),
                        "дом",
                        "rest",
                        "Rest",
                    ),
                )
                pending = ", ".join(
                    t["kind"] for t in engine.tasks() if t["status"] == "pending"
                )
                branch_providers = RuntimeProviders(
                    branch_db, Path("config"), clock=lambda at=at: at
                )
                try:
                    await branch_providers.mood.apply_life_effect(
                        at + timedelta(minutes=1)
                    )
                    mood_after = await branch_providers.mood.current(
                        at + timedelta(minutes=1)
                    )
                    pad = ", ".join(
                        f"{getattr(mood_after, axis):.4f}" for axis in ("P", "A", "D")
                    )
                finally:
                    await branch_providers.close()
                branches.append(
                    f"| {event['outcome']} | {engine.state()['cash']} | "
                    f"{pad} | {engine.needs(at)['economize']} | "
                    f"{event['facts']} | {pending} |"
                )
        finally:
            await runtime.close()
            await providers.close()
    lines = [
        "# Autonomous life simulation",
        "",
        "Offline factual renderer. No models, token estimates or Telegram delivery.",
        "Times use Asia/Almaty. Temporary databases are discarded after verification.",
        "A midpoint runtime restart preserves the day, resource state and receipts.",
        "",
        f"Days: {days}. Final cash: {state['cash']} KZT; debt: {state['debt']} KZT.",
        "Storage counts: `" + json.dumps(counts, sort_keys=True) + "`.",
        "",
        "## Timeline",
        "",
        "| Time | Place | Activity | Event | Publication or silence |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        "| "
        + " | ".join(
            str(value).replace("|", "/").replace("\n", " ") for value in row[1:]
        )
        + " |"
        for row in rows
    )
    lines += [
        "",
        "## Alternative outcomes from the same 1,000 KZT shortfall fixture",
        "",
        "| Outcome | Cash after | PAD after | Economize | Recorded facts | "
        "Pending tasks |",
        "| --- | --- | --- | --- | --- | --- |",
        *branches,
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("docs/LIFE_SIMULATION.md"))
    args = parser.parse_args()
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR)
    )
    counts = asyncio.run(simulate(args.days, args.output))
    print(json.dumps({"report": str(args.output), "counts": counts}))


if __name__ == "__main__":
    main()
