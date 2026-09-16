"""Release checks reject private files and recognize unlabelled bot credentials."""

import runpy
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "name",
    [
        "library/topics.yaml",
        "library/nested/article.md",
        "config/telegram.yaml",
        "config/telegram.yml",
        "config/world-state.json",
        "config/world-override.json",
        ".env",
        ".env.production",
        "config/bots.env",
        "config/.env.local",
    ],
)
def test_repository_audit_rejects_private_paths(name):
    audit = runpy.run_path(str(ROOT / "scripts/check_repository.py"))
    assert audit["private_paths"]([name]) == [name]


def test_repository_audit_allows_public_templates_and_source():
    audit = runpy.run_path(str(ROOT / "scripts/check_repository.py"))
    assert (
        audit["private_paths"](
            [
                "config/.env.example",
                "src/core/telegram.py",
                "mika-startup/README.md",
                "mika-startup/startup/config/telegram.yaml",
                "mika-startup/startup/config/world-state.json",
                "mika-startup/startup/config/world-override.json",
            ]
        )
        == []
    )


def test_repository_audit_checks_staged_files_including_forced_additions(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("/library/\n")
    (tmp_path / "library").mkdir()
    (tmp_path / "library/private.md").write_text("Private fixture article")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-f", "library/private.md"], check=True
    )
    audit = runpy.run_path(str(ROOT / "scripts/check_repository.py"))
    assert audit["private_paths"](audit["tracked_paths"](tmp_path)) == [
        "library/private.md"
    ]


@pytest.mark.parametrize("ending", ["A", "_", "-"])
def test_secret_scan_rejects_unlabelled_telegram_tokens_without_echoing(ending):
    token = "1234567890:" + "A" * 34 + ending
    result = subprocess.run(
        [
            "gitleaks",
            "stdin",
            "--config",
            str(ROOT / "config/gitleaks.toml"),
            "--redact=100",
            "--no-banner",
            "--verbose",
        ],
        input=f'"{token}"\n',
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert token not in result.stdout + result.stderr
    assert "telegram-bot-token" in result.stdout + result.stderr
