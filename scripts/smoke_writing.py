"""Exercise live local draft generation with explicit fixture world inputs."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from src.core.context import ContextBuilder
from src.core.db import Database
from src.core.llm_local import LocalLLM
from src.core.logging import configure_logging
from src.core.mood import BaselineContext, MoodModel
from src.core.schedule import SleepWindow
from src.core.settings import SettingsRegistry, SQLiteSettingsStore
from src.core.time_utils import now
from src.core.world import World
from src.ingest import read_source
from src.validator import OutputValidator
from src.writer import Writer


async def run(args):
    database = Database(args.database)
    database.initialize()
    at = now()
    world = World.from_config(Path("config"))
    model = MoodModel.from_config(Path("config"))
    # These are test inputs, never inferred production observations.
    day = world.day_context(
        at,
        sleep=SleepWindow(at.replace(hour=1, minute=0), at.replace(hour=9, minute=0)),
        sleep_debt=0,
        location=args.location,
        road_roll=0.99,
    )
    context = ContextBuilder(Path("prompts"), database=database, mood_model=model)
    reports = []
    settings = SettingsRegistry.from_file(
        args.settings, store=SQLiteSettingsStore(database)
    )
    async with LocalLLM(database=database, settings=settings) as llm:
        writer = Writer(database, llm, context, OutputValidator(llm))
        for path in args.articles:
            source = read_source(path)
            result = await writer.generate(
                "found",
                day=day,
                mood=model.baseline(at, BaselineContext()),
                wake_reason="fixture",
                article_title=source.title,
                article_source=source.publisher or source.origin_key,
                article_kind=source.kind,
                given_by=source.given_by,
                topic=source.topic,
            )
            reports.append(asdict(result) | {"source_id": source.id})
    args.report.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(reports, ensure_ascii=False))
    return 0 if all(row["status"] == "draft" for row in reports) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("articles", type=Path, nargs="+")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--settings", type=Path, default=Path("config/settings.yaml"))
    args = parser.parse_args()
    configure_logging(args.log_file)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
