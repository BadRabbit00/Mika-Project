"""Command-line entry point for the storage foundation."""

import argparse
from pathlib import Path

import structlog

from src.core.db import Database
from src.core.logging import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BlogAI storage administration")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init-db", help="Create or migrate a database")
    initialize.add_argument("--database", type=Path, required=True)
    initialize.add_argument("--log-file", type=Path, required=True)
    args = parser.parse_args(argv)
    configure_logging()
    try:
        configure_logging(args.log_file)
        Database(args.database).initialize()
    except Exception:
        structlog.get_logger("blogai.cli").exception("initialization_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
