"""File-backed bot credentials preserve environment precedence and secrecy."""

import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import structlog

from src import cli, telegram_runtime
from src.core.telegram import TelegramLayout, load_bot_tokens


@pytest.fixture
def layout(monkeypatch, tmp_path):
    layout = TelegramLayout(
        owner_id=123,
        group_id=-100123,
        topics=dict(
            diary=1, author=2, curator=3, chat=4, library=5, machine=6, control=7
        ),
    )
    for name in (*layout.bots.values(), "CUSTOM_MIKA_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return layout


def write_tokens(path, layout):
    tokens = {
        role: f"{index}:fixture-{role}" for index, role in enumerate(layout.bots, 1)
    }
    path.write_text(
        "\n".join(f"{name}={tokens[role]}" for role, name in layout.bots.items()),
        encoding="utf-8",
    )
    return tokens


def test_default_env_file_loads_three_tokens_without_mutating_environment(layout):
    expected = write_tokens(Path(".env"), layout)
    assert load_bot_tokens(layout) == expected
    assert all(name not in os.environ for name in layout.bots.values())


def test_explicit_file_supports_quotes_comments_export_and_custom_names(layout):
    layout.bots["mika"] = "CUSTOM_MIKA_TOKEN"
    path = Path("bots.env")
    path.write_text(
        "\ufeff# Local bot credentials\n"
        "export CUSTOM_MIKA_TOKEN = '1:fixture-mika'\n"
        'CURATOR_BOT_TOKEN="2:fixture-curator" # curator\n'
        "OPS_BOT_TOKEN=3:fixture-ops\n",
        encoding="utf-8",
    )
    assert load_bot_tokens(layout, path) == {
        "mika": "1:fixture-mika",
        "curator": "2:fixture-curator",
        "ops": "3:fixture-ops",
    }


def test_exported_environment_overrides_file(layout, monkeypatch):
    expected = write_tokens(Path(".env"), layout)
    monkeypatch.setenv("MIKA_BOT_TOKEN", "9:exported-mika")
    expected["mika"] = "9:exported-mika"
    assert load_bot_tokens(layout) == expected


def test_environment_only_startup_without_default_file(layout, monkeypatch):
    for role, name in layout.bots.items():
        monkeypatch.setenv(name, f"fixture-{role}")
    assert load_bot_tokens(layout) == {role: f"fixture-{role}" for role in layout.bots}
    with pytest.raises(FileNotFoundError):
        load_bot_tokens(layout, Path("missing.env"))


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_empty_tokens_report_only_variable_names(layout, value):
    path = Path(".env")
    secrets = write_tokens(path, layout)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\nOPS_BOT_TOKEN" + ("" if value is None else f"='{value}'"))
    with pytest.raises(ValueError, match="OPS_BOT_TOKEN") as error:
        load_bot_tokens(layout)
    assert all(secret not in str(error.value) for secret in secrets.values())


def test_empty_exported_token_does_not_fall_back_to_file(layout, monkeypatch):
    write_tokens(Path(".env"), layout)
    monkeypatch.setenv("MIKA_BOT_TOKEN", "")
    with pytest.raises(ValueError, match="MIKA_BOT_TOKEN"):
        load_bot_tokens(layout)


def test_duplicate_tokens_are_rejected_without_echoing_values(layout):
    path = Path(".env")
    tokens = write_tokens(path, layout)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"\nOPS_BOT_TOKEN={tokens['mika']}")
    with pytest.raises(ValueError, match="Three distinct") as error:
        load_bot_tokens(layout)
    assert tokens["mika"] not in str(error.value)


def test_env_values_are_literal_and_never_execute_shell_commands(layout):
    path = Path(".env")
    write_tokens(path, layout)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\nMIKA_BOT_TOKEN='${OPS_BOT_TOKEN}$(touch injected)'\n")
    assert load_bot_tokens(layout)["mika"] == "${OPS_BOT_TOKEN}$(touch injected)"
    assert not Path("injected").exists()


def test_invalid_encoding_does_not_expose_file_contents(layout):
    Path(".env").write_bytes(b"private-fixture-secret\xff")
    with pytest.raises(ValueError, match="UTF-8") as error:
        load_bot_tokens(layout)
    assert "private-fixture-secret" not in str(error.value)


@pytest.mark.parametrize("command", ["run", "bot"])
@pytest.mark.parametrize("explicit", [False, True])
def test_cli_passes_env_file_to_telegram(tmp_path, monkeypatch, command, explicit):
    monkeypatch.setattr("src.catalogue.Catalogue.load", Mock())
    run = AsyncMock()
    monkeypatch.setattr(cli, "run_telegram", run)
    arguments = [
        command,
        "--layout",
        str(tmp_path / "layout.yaml"),
        "--log-file",
        str(tmp_path / "run.jsonl"),
        "--database",
        str(tmp_path / "mika.db"),
    ]
    if explicit:
        arguments.extend(["--env-file", str(tmp_path / "bots.env")])
    assert cli.main(arguments) == 0
    run.assert_awaited_once()
    assert run.call_args.args[0].env_file == (
        tmp_path / "bots.env" if explicit else None
    )


async def test_runtime_loads_all_three_tokens_and_redacts_logs(layout, monkeypatch):
    path = Path("bots.env")
    tokens = write_tokens(path, layout)
    monkeypatch.setattr(TelegramLayout, "from_file", Mock(return_value=layout))
    bots = Mock()
    monkeypatch.setattr(telegram_runtime, "Bot", bots)
    monkeypatch.setattr(
        telegram_runtime, "Database", Mock(side_effect=RuntimeError("startup boundary"))
    )
    original = structlog.get_config().copy()
    logger = logging.getLogger("blogai")
    handlers, level, propagate = logger.handlers[:], logger.level, logger.propagate
    logger.handlers = []
    try:
        with pytest.raises(RuntimeError, match="startup boundary"):
            await telegram_runtime.run_telegram(
                SimpleNamespace(
                    layout=Path("layout.yaml"),
                    env_file=path,
                    log_file=Path("run.jsonl"),
                    database=Path("mika.db"),
                )
            )
        assert [call.args[0] for call in bots.call_args_list] == list(tokens.values())
        structlog.get_logger("blogai.fixture").error(
            "token_failure_fixture", detail=" ".join(tokens.values())
        )
        log = Path("run.jsonl").read_text(encoding="utf-8")
        assert "telegram_credentials_loaded" in log
        assert "[REDACTED]" in log
        assert all(token not in log for token in tokens.values())
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate
        structlog.configure(**original)
