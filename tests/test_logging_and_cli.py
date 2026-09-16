"""Observable storage changes and an executable, logged initialization command."""

import json
import logging
import subprocess
import sys

import pytest
import structlog

from src.core.db import Database
from src.core.logging import configure_logging
from src.core.time_utils import from_utc_iso


@pytest.fixture
def log_path(tmp_path):
    original = structlog.get_config().copy()
    logger = logging.getLogger("blogai")
    old_handlers, old_level, old_propagate = (
        logger.handlers[:],
        logger.level,
        logger.propagate,
    )
    logger.handlers = []
    path = tmp_path / "events.jsonl"
    configure_logging(path, level=logging.CRITICAL)
    yield path
    for handler in logger.handlers:
        handler.close()
    logger.handlers = old_handlers
    logger.setLevel(old_level)
    logger.propagate = old_propagate
    structlog.configure(**original)


def test_jsonl_contains_row_diffs_and_commit_outcome(tmp_path, log_path):
    database = Database(tmp_path / "logged.sqlite3")
    database.initialize()

    def write(connection):
        connection.execute("INSERT INTO nodes(id, name) VALUES ('n', 'Initial')")
        connection.execute("UPDATE nodes SET name = 'Updated' WHERE id = 'n'")
        connection.execute("DELETE FROM nodes WHERE id = 'n'")

    database.run_transaction(write)
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    for record in records:
        assert from_utc_iso(record["at"]).utcoffset() is not None
    changes = [record for record in records if record["event"] == "db_row_change"]
    assert [record["operation"] for record in changes] == ["insert", "update", "delete"]
    assert changes[0]["before"] is None
    assert changes[1]["before"]["name"] == "Initial"
    assert changes[1]["after"]["name"] == "Updated"
    assert changes[2]["after"] is None
    assert len({record["transaction_id"] for record in changes}) == 1
    assert any(
        record["event"] == "db_transaction_committed"
        and record["transaction_id"] == changes[0]["transaction_id"]
        for record in records
    )


def test_rollback_logs_attempted_changes_and_traceback(tmp_path, log_path):
    database = Database(tmp_path / "rollback.sqlite3")
    database.initialize()

    def fail(connection):
        connection.execute("INSERT INTO topics(name) VALUES ('rolled-back')")
        raise RuntimeError("Intentional failure")

    with pytest.raises(RuntimeError):
        database.run_transaction(fail)
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    change = next(record for record in records if record["event"] == "db_row_change")
    rollback = next(
        record for record in records if record["event"] == "db_transaction_rolled_back"
    )
    assert change["outcome"] == "attempted"
    assert change["transaction_id"] == rollback["transaction_id"]
    assert "RuntimeError: Intentional failure" in rollback["exception"]


def test_blob_row_changes_can_be_logged(tmp_path, log_path):
    database = Database(tmp_path / "embedding.sqlite3")
    database.initialize()

    def write(connection):
        connection.execute("INSERT INTO nodes(id, name) VALUES ('n', 'Node')")
        connection.execute(
            "INSERT INTO node_embeddings(node_id, embedding) VALUES ('n', ?)",
            (b"\x00\xff",),
        )

    database.run_transaction(write)
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    change = next(
        record for record in records if record.get("table") == "node_embeddings"
    )
    assert change["after"]["embedding"] == {"blob_hex": "00FF"}


def test_logging_redacts_secret_values_and_keys(log_path):
    configure_logging(
        log_path, level=logging.CRITICAL, secret_values=("fixture-secret",)
    )
    structlog.get_logger("blogai.fixture").error(
        "secret_test",
        api_key="hidden-value",
        detail="failed fixture-secret",
        tokens_in=123,
    )
    text = log_path.read_text()
    assert "fixture-secret" not in text and "hidden-value" not in text
    assert json.loads(text.splitlines()[-1])["tokens_in"] == 123


def test_init_db_cli_is_repeatable(tmp_path):
    path = tmp_path / "cli.sqlite3"
    log_file = tmp_path / "cli.jsonl"
    command = [
        sys.executable,
        "-m",
        "src.cli",
        "init-db",
        "--database",
        str(path),
        "--log-file",
        str(log_file),
    ]
    for _ in range(2):
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
    assert path.is_file()
    records = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert (
        len([record for record in records if record["event"] == "db_initialized"]) == 2
    )


def test_init_db_cli_reports_failure_with_nonzero_exit(tmp_path):
    invalid_path = tmp_path / "directory"
    invalid_path.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.cli",
            "init-db",
            "--database",
            str(invalid_path),
            "--log-file",
            str(tmp_path / "failure.jsonl"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    records = [json.loads(line) for line in result.stderr.splitlines()]
    error = next(
        record for record in records if record["event"] == "initialization_failed"
    )
    assert "exception" in error


def test_quiz_cli_empty_topic_needs_no_model(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.cli",
            "quiz",
            "--topic",
            "unknown",
            "--question",
            "Unknown fact?",
            "--database",
            str(tmp_path / "quiz.sqlite3"),
            "--log-file",
            str(tmp_path / "quiz.jsonl"),
            "--min-similarity",
            "0.8",
            "--rrf-k",
            "60",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verdict"] == "no_knowledge"
