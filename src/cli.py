"""Logged commands for storage, extraction, and self-quiz."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import structlog

from src.core.context import ContextBuilder
from src.core.db import Database
from src.core.llm_local import LocalLLM
from src.core.logging import configure_logging
from src.extract import Extractor
from src.ingest import read_source
from src.retrieve import RetrievalPolicy, Retriever
from src.selfquiz import QuizSettings, SelfQuiz


async def _extract(args):
    database = Database(args.database)
    await asyncio.to_thread(database.initialize)
    async with LocalLLM(args.generation_url, args.embedding_url) as llm:
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
    policy = RetrievalPolicy(args.min_similarity, args.rrf_k)
    settings = QuizSettings.from_registry(args.settings)
    async with LocalLLM(args.generation_url, args.embedding_url) as llm:
        service = SelfQuiz(
            database,
            llm,
            Retriever(database, llm, policy),
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
    quiz.add_argument("--min-similarity", type=float, required=True)
    quiz.add_argument("--rrf-k", type=float, required=True)
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
        configure_logging(args.log_file)
        if args.command == "init-db":
            Database(args.database).initialize()
        elif args.command == "extract":
            asyncio.run(_extract(args))
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
