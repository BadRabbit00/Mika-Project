"""Atomic session receipts in the documented session and personal-memory tables."""

import json
from uuid import uuid4

from src.core.time_utils import require_aware, to_utc_iso

_CONFIRMED = """EXISTS (
    SELECT 1 FROM outbox o WHERE o.sent_at IS NOT NULL
    AND o.tg_message_id IS NOT NULL
    AND o.channel=CASE s.channel WHEN 'dm' THEN 'chat-dm' ELSE 'chat' END
    AND json_extract(o.payload,'$.trace_id')=t.trace_id
    AND json_extract(o.payload,'$.method')='message'
    AND json_extract(o.payload,'$.destination.bot')='mika'
    AND json_extract(o.payload,'$.text')=t.text
)"""


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
                    "SELECT t.* FROM session_turns t "
                    "JOIN sessions s ON s.id=t.session_id WHERE t.session_id=? "
                    f"AND (t.role='user' OR {_CONFIRMED}) ORDER BY t.idx,t.id",
                    (session_id,),
                )
            ]

    def confirmed_summary(self, session):
        """Ignore legacy compression that may include undelivered output."""
        state = summary_state(session)
        if state["through_idx"] < 0:
            return state
        with self.database.connection(readonly=True) as connection:
            hidden = connection.execute(
                "SELECT 1 FROM session_turns t JOIN sessions s ON s.id=t.session_id "
                "WHERE t.session_id=? AND t.idx<=? AND t.role='mika' "
                f"AND NOT ({_CONFIRMED}) LIMIT 1",
                (session["id"], state["through_idx"]),
            ).fetchone()
        if hidden:
            state.update(text="", through_idx=-1)
        return state

    def mood_events(self, session, turns):
        until = session["closed_at"] or (
            turns[-1]["at"] if turns else session["opened_at"]
        )
        with self.database.connection() as c:
            return [
                dict(row)
                for row in c.execute(
                    "SELECT at,p,a,d FROM mood WHERE "
                    "julianday(at)>=julianday(?) AND julianday(at)<=julianday(?) "
                    "ORDER BY julianday(at),rowid",
                    (session["opened_at"], until),
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

    def staged_reply(self, trace_id):
        with self.database.connection(readonly=True) as connection:
            row = connection.execute(
                "SELECT * FROM chat_replies WHERE trace_id=?", (trace_id,)
            ).fetchone()
            if row is None:
                return None
            payload = json.loads(row["payload"])
            return dict(
                payload,
                session_id=row["session_id"],
                trace_id=trace_id,
                cited=json.dumps(payload["cited"]),
            )

    def stage_reply(self, session_id, *, trace_id, at, **payload):
        """Persist validated output separately from the visible dialogue."""
        at = require_aware(at)
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        def save(connection):
            session = connection.execute(
                "SELECT closed_at FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None or session[0] is not None:
                raise ValueError("The session closed during generation")
            existing = {
                row[0]
                for row in connection.execute("SELECT id FROM nodes WHERE suspect=0")
            }
            if not set(payload["cited"]) <= existing:
                raise ValueError("A cited node changed during generation")
            previous = connection.execute(
                "SELECT session_id,payload FROM chat_replies WHERE trace_id=?",
                (trace_id,),
            ).fetchone()
            if previous and tuple(previous) != (session_id, serialized):
                raise ValueError("A reply trace cannot identify changed output")
            connection.execute(
                "INSERT OR IGNORE INTO chat_replies"
                "(trace_id,session_id,user_id,payload,created_at) VALUES (?,?,?,?,?)",
                (trace_id, session_id, payload["user_id"], serialized, at),
            )

        self.database.run_transaction(save)
        return self.staged_reply(trace_id)

    @staticmethod
    def bind_delivery(connection, trace_id, payload, outbox_id):
        row = connection.execute(
            "SELECT r.*,s.channel FROM chat_replies r "
            "JOIN sessions s ON s.id=r.session_id WHERE r.trace_id=?",
            (trace_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Chat delivery requires a validated draft")
        channel = "chat-dm" if row["channel"] == "dm" else "chat"
        if (
            payload["method"] != "message"
            or payload.get("text") != json.loads(row["payload"])["text"]
            or payload["trace_id"] != trace_id
            or payload["destination"]["bot"] != "mika"
            or payload["destination"]["channel"] != channel
            or row["outbox_id"] not in (None, outbox_id)
        ):
            raise ValueError("Chat delivery differs from its validated draft")
        connection.execute(
            "UPDATE chat_replies SET outbox_id=? WHERE trace_id=?",
            (outbox_id, trace_id),
        )

    def confirm_reply(self, connection, trace_id, *, outbox_id, at):
        """Commit a delivered turn inside the same transaction as its receipt."""
        row = connection.execute(
            "SELECT * FROM chat_replies WHERE trace_id=?", (trace_id,)
        ).fetchone()
        if row is None:
            raise ValueError("No validated reply exists for this receipt")
        if row["outbox_id"] != outbox_id:
            raise ValueError("Receipt does not match the bound reply delivery")
        if row["sent_at"] is not None:
            return
        self.save_reply(
            row["session_id"],
            trace_id=trace_id,
            at=at,
            **json.loads(row["payload"]),
            _connection=connection,
        )
        connection.execute(
            "UPDATE chat_replies SET sent_at=?,outbox_id=? WHERE trace_id=?",
            (to_utc_iso(at), outbox_id, trace_id),
        )

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
        _connection=None,
    ):
        at = require_aware(at)

        def save(c):
            session = c.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None or (session["closed_at"] and _connection is None):
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
            if _connection is None and not set(cited) <= existing:
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

        return (
            save(_connection)
            if _connection is not None
            else self.database.run_transaction(save)
        )

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
