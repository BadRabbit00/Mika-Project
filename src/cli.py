"""Command-line entry point for the storage foundation."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import structlog

from src.core.context import ContextBuilder
from src.core.db import Database
from src.core.llm_local import LocalLLM
from src.core.logging import configure_logging
from src.extract import Extractor
from src.ingest import read_source


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BlogAI storage administration")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init-db", help="Create or migrate a database")
    initialize.add_argument("--database", type=Path, required=True)
    initialize.add_argument("--log-file", type=Path, required=True)
    extract = commands.add_parser(
        "extract", help="Extract grounded claims from Markdown articles"
    )
    extract.add_argument("articles", type=Path, nargs="+")
    extract.add_argument("--database", type=Path, required=True)
    extract.add_argument("--log-file", type=Path, required=True)
    extract.add_argument("--prompt-dir", type=Path, default=Path("prompts"))
    extract.add_argument("--grammar-dir", type=Path, default=Path("grammars"))
    extract.add_argument("--generation-url", default="http://127.0.0.1:8080")
    extract.add_argument("--embedding-url", default="http://127.0.0.1:8081")
    extract.add_argument("--max-output-tokens", type=int, default=2048)
    args = parser.parse_args(argv)
    configure_logging()
    try:
        configure_logging(args.log_file)
        if args.command == "init-db":
            Database(args.database).initialize()
        else:
            asyncio.run(_extract(args))
    except Exception:
        structlog.get_logger("blogai.cli").exception("initialization_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
