"""Logged commands for storage, extraction, and self-quiz."""

import argparse
import asyncio
import json
import logging
import tempfile
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import structlog

from src.core.context import ContextBuilder
from src.core.db import Database
from src.core.llm_local import LocalLLM
from src.core.llm_vendor import ClaudeCodeBackend, VendorConfig
from src.core.logging import configure_logging
from src.core.settings import SettingsRegistry, SQLiteSettingsStore
from src.curator import Curator
from src.extract import Extractor
from src.ingest import read_source
from src.retrieve import RetrievalPolicy, Retriever
from src.runtime import dry_run
from src.selfquiz import SelfQuiz
from src.telegram_runtime import run_telegram


async def _curator(args):
    database = Database(args.database)
    await asyncio.to_thread(database.initialize)
    config = VendorConfig.from_registry(args.settings, timeout_sec=args.timeout)
    curator = Curator(database, ClaudeCodeBackend(config), args.prompt_dir)
    data = json.loads(args.context.read_text(encoding="utf-8"))
    if args.action == "grade":
        data["answers"] = {int(key): value for key, value in data["answers"].items()}
    method = {
        "exam": curator.prepare_exam,
        "grade": curator.grade,
        "select": curator.select_articles,
    }[args.action]
    with structlog.contextvars.bound_contextvars(trace_id=args.trace_id):
        result = await method(trace_id=args.trace_id, **data)
    print(json.dumps(result, ensure_ascii=False))


async def _extract(args):
    database = Database(args.database)
    await asyncio.to_thread(database.initialize)
    async with LocalLLM(
        args.generation_url, args.embedding_url, database=database
    ) as llm:
        extractor = Extractor(
            database,
            llm,
            ContextBuilder(args.prompt_dir),
            grammar_dir=args.grammar_dir,
            max_output_tokens=args.max_output_tokens,
        )
        for path in args.articles:
            result = await extractor.extract(read_source(path))
            print(json.dumps(asdict(result)))


async def _quiz(args):
    database = Database(args.database)
    await asyncio.to_thread(database.initialize)
    settings = SettingsRegistry.from_file(
        args.settings, store=SQLiteSettingsStore(database)
    )
    policy = (
        RetrievalPolicy(
            args.min_similarity
            if args.min_similarity is not None
            else settings.get("retrieval.min_similarity"),
            args.rrf_k if args.rrf_k is not None else settings.get("retrieval.rrf_k"),
        )
        if args.min_similarity is not None or args.rrf_k is not None
        else None
    )
    async with LocalLLM(
        args.generation_url, args.embedding_url, database=database
    ) as llm:
        service = SelfQuiz(
            database,
            llm,
            Retriever(database, llm, policy, settings=settings),
            ContextBuilder(args.prompt_dir),
            settings,
            grammar_dir=args.grammar_dir,
            max_output_tokens=args.max_output_tokens,
        )
        with structlog.contextvars.bound_contextvars(trace_id=uuid4().hex):
            result = (
                await service.answer(args.question, topic=args.topic)
                if args.question is not None
                else await service.run(args.topic)
            )
        print(json.dumps(asdict(result), ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BlogAI storage and learning commands")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run the assembled application in test mode")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--workdir", type=Path)
    run.add_argument("--log-file", type=Path)
    initialize = commands.add_parser("init-db", help="Create or migrate a database")
    initialize.add_argument("--database", type=Path, required=True)
    initialize.add_argument("--log-file", type=Path, required=True)
    extract = commands.add_parser(
        "extract", help="Extract grounded claims from Markdown articles"
    )
    extract.add_argument("articles", type=Path, nargs="+")
    quiz = commands.add_parser("quiz", help="Check knowledge using graph citations")
    quiz.add_argument("--topic", required=True)
    quiz.add_argument(
        "--question", help="Answer one supplied question instead of a round"
    )
    quiz.add_argument("--settings", type=Path, default=Path("config/settings.yaml"))
    quiz.add_argument("--min-similarity", type=float)
    quiz.add_argument("--rrf-k", type=float)
    curator = commands.add_parser("curator", help="Run a file-backed curator workflow")
    curator.add_argument("action", choices=("exam", "grade", "select"))
    curator.add_argument("--context", type=Path, required=True)
    curator.add_argument("--database", type=Path, required=True)
    curator.add_argument("--log-file", type=Path, required=True)
    curator.add_argument("--settings", type=Path, default=Path("config/settings.yaml"))
    curator.add_argument("--prompt-dir", type=Path, default=Path("prompts"))
    curator.add_argument("--timeout", type=int, required=True)
    curator.add_argument("--trace-id", required=True)
    bot = commands.add_parser("bot", help="Run three Telegram bots and background jobs")
    bot.add_argument("--layout", type=Path, required=True)
    bot.add_argument(
        "--interface-storage",
        action="store_true",
        help="Use explicitly installed settings and lineage tables",
    )
    bot.add_argument("--database", type=Path, required=True)
    bot.add_argument("--log-file", type=Path, required=True)
    bot.add_argument("--library", type=Path, default=Path("library"))
    bot.add_argument("--settings", type=Path, default=Path("config/settings.yaml"))
    bot.add_argument("--prompt-dir", type=Path, default=Path("prompts"))
    bot.add_argument("--grammar-dir", type=Path, default=Path("grammars"))
    bot.add_argument("--generation-url", default="http://127.0.0.1:8080")
    bot.add_argument("--embedding-url", default="http://127.0.0.1:8081")
    for command in (extract, quiz):
        command.add_argument("--database", type=Path, required=True)
        command.add_argument("--log-file", type=Path, required=True)
        command.add_argument("--prompt-dir", type=Path, default=Path("prompts"))
        command.add_argument("--grammar-dir", type=Path, default=Path("grammars"))
        command.add_argument("--generation-url", default="http://127.0.0.1:8080")
        command.add_argument("--embedding-url", default="http://127.0.0.1:8081")
        command.add_argument("--max-output-tokens", type=int, default=2048)
    args = parser.parse_args(argv)
    configure_logging()
    try:
        configure_logging(
            args.log_file,
            level=logging.WARNING if args.command == "run" else logging.INFO,
        )
        if args.command == "init-db":
            Database(args.database).initialize()
        elif args.command == "extract":
            asyncio.run(_extract(args))
        elif args.command == "curator":
            asyncio.run(_curator(args))
        elif args.command == "bot":
            asyncio.run(run_telegram(args))
        elif args.command == "run":
            if not args.dry_run:
                raise ValueError(
                    "TODO(LIVE-RUNNER): storage and runtime contracts are required"
                )
            if args.workdir:
                report = asyncio.run(dry_run(args.workdir))
            else:
                with tempfile.TemporaryDirectory(prefix="blogai-dry-run-") as directory:
                    report = asyncio.run(dry_run(Path(directory)))
                    report["ephemeral"] = True
            print(json.dumps(report, ensure_ascii=False))
        else:
            asyncio.run(_quiz(args))
    except Exception:
        event = (
            "initialization_failed" if args.command == "init-db" else "command_failed"
        )
        structlog.get_logger("blogai.cli").exception(event, command=args.command)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
