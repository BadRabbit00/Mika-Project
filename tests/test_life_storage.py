"""Persistent simulation, delivery, and compatible upgrade invariants."""

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from src.core.db import SCHEMA_VERSION, Database
from src.core.time_utils import ALMATY, to_utc_iso

AT = datetime(2026, 9, 16, 8, tzinfo=ALMATY)


@pytest.fixture
def database(tmp_path):
    result = Database(tmp_path / "life.sqlite3")
    result.initialize()
    return result


def test_life_upgrade_preserves_existing_simulation_and_messages(tmp_path):
    database = Database(tmp_path / "upgrade.sqlite3")
    database.migrate(target_version=13)

    def seed(c):
        c.execute("INSERT INTO nodes(id,name) VALUES ('known','Known fact')")
        c.execute(
            "INSERT INTO sources(id,path,title) VALUES ('read','article.md','Read')"
        )
        c.execute("INSERT INTO mood(at,p,a,d) VALUES (?,0.1,0.2,0.3)", (AT,))
        c.execute(
            "INSERT INTO life_state(key,value,updated_at) "
            "VALUES ('series.episode','3',?)",
            (AT,),
        )
        c.execute(
            "INSERT INTO life_journal(at,slot,entity,text) "
            "VALUES (?,'home','old','Recorded event')",
            (AT,),
        )
        c.execute(
            "INSERT INTO sessions(id,channel,opened_at) VALUES ('s','dm',?)", (AT,)
        )
        c.execute(
            "INSERT INTO session_turns(session_id,idx,role,text,at) "
            "VALUES ('s',0,'user','Hello',?)",
            (AT,),
        )
        c.execute(
            "INSERT INTO posts(id,kind,state,text) "
            "VALUES ('p','daily','draft','A saved draft')"
        )
        c.execute(
            "INSERT INTO outbox(idem_key,payload,next_try_at) VALUES ('old',?,?)",
            (json.dumps({"text": "Saved delivery"}), AT),
        )

    database.run_transaction(seed)
    tables = (
        "nodes",
        "sources",
        "mood",
        "life_state",
        "life_journal",
        "sessions",
        "session_turns",
        "posts",
        "outbox",
    )

    def snapshot():
        with database.connection(readonly=True) as c:
            return {
                table: [dict(row) for row in c.execute(f"SELECT * FROM {table}")]
                for table in tables
            }

    before = snapshot()
    assert database.initialize() == SCHEMA_VERSION
    assert snapshot() == before
    with database.connection(readonly=True) as c:
        installed = {
            row[0]
            for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "life_days",
            "life_activities",
            "life_tasks",
            "life_events",
            "life_effects",
            "money_ledger",
            "chat_inbox",
            "chat_replies",
        } <= installed
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def add_activity(c, identity, start, end):
    c.execute(
        "INSERT INTO life_activities(id,day,starts_at,ends_at,location,kind,label,"
        "can_publish,can_chat,can_study) VALUES (?,'2026-09-16',?,?,'дом','rest',"
        "'Resting at home',1,1,1)",
        (identity, start, end),
    )


def test_persisted_activities_reject_overlap_and_naive_instants(database):
    def seed(c):
        c.execute(
            "INSERT INTO life_days(day,seed,config_json,created_at) "
            "VALUES ('2026-09-16','fixed','{}',?)",
            (AT,),
        )
        add_activity(c, "first", AT, AT + timedelta(hours=1))
        add_activity(c, "second", AT + timedelta(hours=1), AT + timedelta(hours=2))

    database.run_transaction(seed)
    for start, end in (
        (AT + timedelta(minutes=30), AT + timedelta(hours=2)),
        (AT, AT),
        (to_utc_iso(AT)[:-1], to_utc_iso(AT + timedelta(minutes=1))),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            database.run_transaction(
                lambda c, start=start, end=end: add_activity(c, "bad", start, end)
            )
    with pytest.raises(ValueError):
        database.run_transaction(
            lambda c: add_activity(c, "naive", AT.replace(tzinfo=None), AT)
        )
    with pytest.raises(sqlite3.IntegrityError):
        database.run_transaction(
            lambda c: c.execute(
                "UPDATE life_activities SET starts_at=? WHERE id='second'", (AT,)
            )
        )


def test_money_and_effect_receipts_cannot_be_applied_twice(database):
    def seed(c):
        c.execute(
            "INSERT INTO life_events(id,entity,at,kind,payload) "
            "VALUES ('e','request:one',?,'money','{}')",
            (AT,),
        )
        c.execute(
            "INSERT INTO life_effects(id,event_id,kind,payload) "
            "VALUES ('effect','e','transfer','{}')"
        )
        c.execute(
            "INSERT INTO money_ledger(id,event_id,account,delta,balance_after,at) "
            "VALUES ('transfer','e','cash',100,100,?)",
            (AT,),
        )

    database.run_transaction(seed)
    with pytest.raises(sqlite3.IntegrityError):
        database.run_transaction(
            lambda c: c.execute(
                "INSERT INTO life_events(id,entity,at,kind,payload) "
                "VALUES ('other','request:one',?,'money','{}')",
                (AT,),
            )
        )
    with pytest.raises(sqlite3.IntegrityError):
        database.run_transaction(
            lambda c: c.execute(
                "INSERT INTO money_ledger(id,event_id,account,delta,balance_after,at) "
                "VALUES ('transfer','e','cash',100,200,?)",
                (AT,),
            )
        )
    with pytest.raises(sqlite3.IntegrityError):
        database.run_transaction(
            lambda c: c.execute("UPDATE money_ledger SET delta=0.1 WHERE id='transfer'")
        )
