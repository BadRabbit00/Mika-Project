"""Atomic session receipts in the documented session and personal-memory tables."""

import json
from uuid import uuid4

from src.core.time_utils import require_aware


def summary_state(session):
    raw = session["summary"]
    if not raw:
        return {"text": "", "through_idx": -1, "finalized": False}
    try:
        state = json.loads(raw)
    except ValueError:
        return {"text": raw, "through_idx": -1, "finalized": False}
    if not isinstance(state, dict) or "through_idx" not in state:
        raise ValueError("Invalid session summary checkpoint")
    return state


class SessionStore:
    def __init__(self, database):
        self.database = database

    def active(self, channel):
        if channel not in {"dm", "topic"}:
            raise ValueError("Unknown session channel")
        with self.database.connection() as c:
            rows = c.execute(
                "SELECT * FROM sessions WHERE channel=? AND closed_at IS NULL",
                (channel,),
            ).fetchall()
        if len(rows) > 1:
            raise ValueError("Multiple active sessions in one channel")
        return dict(rows[0]) if rows else None

    def get(self, session_id):
        with self.database.connection() as c:
            row = c.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Unknown session")
        return dict(row)

    def open(self, channel, *, at, mood=""):
        at = require_aware(at)
        if channel not in {"dm", "topic"}:
            raise ValueError("Unknown session channel")
        session_id = uuid4().hex

        def save(c):
            row = c.execute(
                "SELECT * FROM sessions WHERE channel=? AND closed_at IS NULL",
                (channel,),
            ).fetchone()
            if row:
                return dict(row)
            c.execute(
                "INSERT INTO sessions"
                "(id,channel,opened_at,turns,tokens_used,mood_start) "
                "VALUES (?,?,?,0,0,?)",
                (session_id, channel, at, mood),
            )
            return dict(
                c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            )

        return self.database.run_transaction(save)

    def turns(self, session_id, *, include_mood=False):
        with self.database.connection() as c:
            return [
                {key: row[key] for key in row.keys() if include_mood or key != "mood"}
                for row in c.execute(
                    "SELECT * FROM session_turns WHERE session_id=? ORDER BY idx,id",
                    (session_id,),
                )
            ]

    def add_user(self, session_id, text, *, trace_id, at):
        at = require_aware(at)
        if not text.strip() or not trace_id:
            raise ValueError("A user message and trace are required")

        def save(c):
            session = c.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None or session["closed_at"]:
                raise ValueError("The session is closed")
            previous = c.execute(
                "SELECT * FROM session_turns WHERE session_id=? "
                "AND trace_id=? AND role='user'",
                (session_id, trace_id),
            ).fetchone()
            if previous:
                if previous["text"] != text:
                    raise ValueError("A turn trace cannot identify changed text")
                return dict(previous)
            idx = c.execute(
                "SELECT COALESCE(max(idx),-1)+1 FROM session_turns WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            row_id = c.execute(
                "INSERT INTO session_turns(session_id,idx,role,text,at,trace_id) "
                "VALUES (?,?,'user',?,?,?)",
                (session_id, idx, text, at, trace_id),
            ).lastrowid
            c.execute("UPDATE sessions SET turns=turns+1 WHERE id=?", (session_id,))
            return dict(
                c.execute(
                    "SELECT * FROM session_turns WHERE id=?", (row_id,)
                ).fetchone()
            )

        return self.database.run_transaction(save)

    def save_reply(
        self,
        session_id,
        *,
        user_id,
        text,
        mode,
        cited,
        at,
        trace_id,
        tokens,
        topic,
        mood=None,
    ):
        at = require_aware(at)

        def save(c):
            session = c.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None or session["closed_at"]:
                raise ValueError("The session closed during generation")
            previous = c.execute(
                "SELECT * FROM session_turns WHERE session_id=? "
                "AND trace_id=? AND role='mika'",
                (session_id, trace_id),
            ).fetchone()
            if previous:
                return dict(previous)
            existing = {
                row[0] for row in c.execute("SELECT id FROM nodes WHERE suspect=0")
            }
            if not set(cited) <= existing:
                raise ValueError("A cited node changed during generation")
            idx = c.execute(
                "SELECT COALESCE(max(idx),-1)+1 FROM session_turns WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            row_id = c.execute(
                "INSERT INTO session_turns"
                "(session_id,idx,role,text,mode,cited,at,trace_id,mood) "
                "VALUES (?,?,'mika',?,?,?,?,?,?)",
                (session_id, idx, text, mode, json.dumps(cited), at, trace_id, mood),
            ).lastrowid
            c.execute("UPDATE session_turns SET mode=? WHERE id=?", (mode, user_id))
            c.execute(
                "UPDATE sessions SET turns=turns+1,tokens_used=tokens_used+?,"
                "mood_start=COALESCE(NULLIF(mood_start,''),?),"
                "mood_end=COALESCE(?,mood_end) "
                "WHERE id=?",
                (tokens, mood, mood, session_id),
            )
            if mode == "unknown":
                question = c.execute(
                    "SELECT text FROM session_turns WHERE id=?", (user_id,)
                ).fetchone()[0]
                c.execute(
                    "INSERT INTO threads(opened_at,kind,text,topic,status,channel) "
                    "VALUES (?,'question',?,?,'open',?)",
                    (
                        at,
                        question,
                        topic,
                        "dm" if session["channel"] == "dm" else "public",
                    ),
                )
            return dict(
                c.execute(
                    "SELECT * FROM session_turns WHERE id=?", (row_id,)
                ).fetchone()
            )

        return self.database.run_transaction(save)

    def checkpoint(self, session_id, state):
        self.database.run_transaction(
            lambda c: c.execute(
                "UPDATE sessions SET summary=? WHERE id=? AND closed_at IS NULL",
                (json.dumps(state, ensure_ascii=False), session_id),
            )
        )

    def begin_close(self, session_id, at, *, mood=None):
        self.database.run_transaction(
            lambda c: c.execute(
                "UPDATE sessions SET mood_end=CASE WHEN closed_at IS NULL "
                "THEN COALESCE(?,mood_end) ELSE mood_end END, "
                "closed_at=COALESCE(closed_at,?) WHERE id=?",
                (mood, require_aware(at), session_id),
            )
        )

    def finish_close(
        self, session_id, *, summary, facts, person_id, at, mood, trace_id
    ):
        at = require_aware(at)

        def save(c):
            row = dict(
                c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            )
            state = summary_state(row)
            if state.get("finalized"):
                return
            state.update(text=summary, finalized=True)
            c.execute(
                "UPDATE sessions SET summary=?,mood_end=COALESCE(?,mood_end) "
                "WHERE id=?",
                (json.dumps(state, ensure_ascii=False), mood, session_id),
            )
            if summary:
                c.execute(
                    "INSERT INTO narrative(at,kind,gist,trace_id) "
                    "VALUES (?,'chat',?,?)",
                    (at, summary, trace_id),
                )
            for fact in facts:
                c.execute(
                    "INSERT INTO people_facts(person_id,fact,kind,source,at) "
                    "SELECT ?,?,?,?,? WHERE NOT EXISTS "
                    "(SELECT 1 FROM people_facts WHERE person_id=? AND fact=?)",
                    (
                        person_id,
                        fact["fact"],
                        fact["kind"],
                        str(fact["source"]),
                        at,
                        person_id,
                        fact["fact"],
                    ),
                )

        self.database.run_transaction(save)
