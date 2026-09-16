"""Approved storage contracts and upgrades from the previously deployed schema."""

import json
import sqlite3

import pytest

from src.core.db import SCHEMA_VERSION, Database


def test_approved_stores_are_installed_and_upgrade_preserves_call_identity(tmp_path):
    database = Database(tmp_path / "upgrade.sqlite3")
    database.migrate(target_version=4)
    database.run_transaction(
        lambda c: c.execute(
            "INSERT INTO runs(trace_id, params_json) VALUES (?, ?)",
            ("old-call", json.dumps({"trace_id": "chain"})),
        )
    )
    assert database.initialize() == SCHEMA_VERSION
    assert database.initialize() == SCHEMA_VERSION
    with database.connection() as c:
        assert c.execute("SELECT call_id, trace_id FROM runs").fetchone()[:] == (
            "old-call",
            "chain",
        )
        c.execute("INSERT INTO runs(call_id, trace_id) VALUES ('next-call', 'chain')")
        tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master")}
        assert {
            "settings_overrides",
            "post_nodes",
            "post_threads",
            "learner_state",
            "learning_events",
            "learning_actions",
            "activity_reservations",
            "curator_review",
            "diary_comments",
            "sleep_log",
            "exam_runs",
        } <= tables
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_calendar_publication_date_is_not_an_instant(tmp_path):
    database = Database(tmp_path / "dates.sqlite3")
    database.initialize()
    with database.connection() as c:
        c.execute(
            "INSERT INTO sources(id,path,title,published_at) VALUES ('s','s','s',?)",
            ("2026-09-16",),
        )
        assert (
            c.execute("SELECT published_at FROM sources").fetchone()[0] == "2026-09-16"
        )
        for invalid in ("2026-02-30", "2026-09-16T00:00:00Z", "20260916"):
            with pytest.raises(sqlite3.IntegrityError):
                c.execute("UPDATE sources SET published_at=?", (invalid,))


def test_legacy_publication_instant_is_preserved_without_guessing_calendar_date(
    tmp_path,
):
    database = Database(tmp_path / "legacy.sqlite3")
    database.migrate(target_version=4)
    with database.connection() as c:
        c.execute(
            "INSERT INTO sources(id,path,title,published_at) "
            "VALUES ('s','s','s','2026-09-15T23:30:00Z')"
        )
    database.initialize()
    with database.connection() as c:
        row = c.execute(
            "SELECT published_at, legacy_published_at FROM sources"
        ).fetchone()
        assert row[:] == (None, "2026-09-15T23:30:00Z")


def test_correction_id_channel_and_sleep_storage_constraints(tmp_path):
    database = Database(tmp_path / "constraints.sqlite3")
    database.initialize()
    with database.connection() as c:
        c.execute(
            "INSERT INTO nodes(id,name,corrected_by) VALUES ('n','N','exam:exam-1')"
        )
        for invalid in ("42", "unknown:1", "exam:"):
            with pytest.raises(sqlite3.IntegrityError):
                c.execute("UPDATE nodes SET corrected_by=?", (invalid,))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO threads(channel) VALUES ('private')")
        c.execute("INSERT INTO threads(channel) VALUES ('dm')")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO sleep_log VALUES "
                "('2026-09-16','2026-09-16','2026-09-16','2026-09-16','alarm',8,0,0)"
            )
