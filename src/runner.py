"""Durable transition/action boundary using sqlite3 and injected async effects."""

import asyncio
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime

import httpx
import structlog

from src.core.time_utils import add_elapsed, from_utc_iso, require_aware, to_utc_iso
from src.orchestrator import Action, Event, Phase, State, transition

log = structlog.get_logger("blogai.runner")
GENERATION_ACTIONS = frozenset({"found", "impression", "struggle", "summary"})
_DUE = (
    "SELECT a.* FROM learning_actions a "
    "WHERE a.status IN ('pending','waiting') AND a.due_at<=? "
    "AND NOT EXISTS (SELECT 1 FROM learner_state WHERE paused_until>?) "
    "AND (a.predecessor IS NULL OR EXISTS (SELECT 1 FROM learning_actions p "
    "WHERE p.id=a.predecessor AND p.status='completed')) "
    "AND (json_extract(a.action_json,'$.kind') NOT IN "
    "('found','impression','struggle','summary') OR NOT EXISTS "
    "(SELECT 1 FROM learner_state WHERE generation_after>?)) "
)


def encode_event(event):
    return json.dumps(asdict(event) | {"at": to_utc_iso(event.at)}, sort_keys=True)


def decode_event(text):
    values = json.loads(text)
    values["at"] = from_utc_iso(values["at"])
    values["article_ids"] = tuple(values.get("article_ids", ()))
    return Event(**values)


def decode_state(text):
    values = json.loads(text)
    values.update(
        phase=Phase(values["phase"]),
        articles=tuple(values["articles"]),
        seen=tuple(values["seen"]),
        pending_articles=tuple(values.get("pending_articles", ())),
    )
    return State(**values)


@dataclass(frozen=True)
class Deferred:
    event: Event
    at: datetime

    def __post_init__(self):
        object.__setattr__(self, "at", require_aware(self.at))
        if self.event.at != self.at:
            raise ValueError("Deferred event and dispatch time must agree")


class SQLiteLearningStore:
    def __init__(self, database, initial):
        self.database = database
        with database.connection() as c:
            tables = {
                row[0]
                for row in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        if not {"learner_state", "learning_events", "learning_actions"} <= tables:
            raise ValueError(
                "TODO(LEARNING-STATE): the explicit runner schema is not installed"
            )
        database.run_transaction(
            lambda c: c.execute(
                "INSERT OR IGNORE INTO learner_state(id,state_json) "
                "VALUES ('learner',?)",
                (json.dumps(asdict(initial)),),
            )
        )

    def state(self):
        with self.database.connection() as c:
            return decode_state(
                c.execute(
                    "SELECT state_json FROM learner_state WHERE id='learner'"
                ).fetchone()[0]
            )

    def _dispatch(self, c, event):
        encoded = encode_event(event)
        prior = c.execute(
            "SELECT event_json FROM learning_events WHERE id=?", (event.id,)
        ).fetchone()
        state = decode_state(
            c.execute(
                "SELECT state_json FROM learner_state WHERE id='learner'"
            ).fetchone()[0]
        )
        if prior:
            if prior[0] != encoded:
                raise ValueError("Event identity was reused with a changed payload")
            return state
        new, actions = transition(state, event)
        snapshot = json.dumps(asdict(new))
        c.execute(
            "INSERT INTO learning_events(id,trace_id,at,event_json,state_json) "
            "VALUES (?,?,?,?,?)",
            (event.id, event.trace_id, event.at, encoded, snapshot),
        )
        c.execute(
            "UPDATE learner_state SET state_json=? WHERE id='learner'", (snapshot,)
        )
        predecessor = None
        for index, action in enumerate(actions):
            identity = f"{event.id}:{index:03}"
            c.execute(
                "INSERT INTO learning_actions"
                "(id,event_id,predecessor,action_json,status,due_at) "
                "VALUES (?,?,?,?,'pending',?)",
                (
                    identity,
                    event.id,
                    predecessor,
                    json.dumps(asdict(action)),
                    event.at,
                ),
            )
            predecessor = identity
        log.info(
            "state_transition",
            trace_id=event.trace_id,
            event_id=event.id,
            previous=state.phase,
            state=new.phase,
            actions=[action.kind for action in actions],
        )
        return new

    def dispatch(self, event):
        return self.database.run_transaction(lambda c: self._dispatch(c, event))

    def actions(self):
        with self.database.connection() as c:
            return [
                dict(row)
                for row in c.execute(
                    "SELECT * FROM learning_actions ORDER BY due_at,id"
                )
            ]

    def peek(self, at):
        at = require_aware(at)
        with self.database.connection() as c:
            row = c.execute(
                _DUE + "ORDER BY a.due_at,a.id LIMIT 1",
                (at, at, at),
            ).fetchone()
        return dict(row) if row else None

    def claim(self, at, identity=None):
        at = require_aware(at)

        def claim(c):
            row = c.execute(
                _DUE + "AND (? IS NULL OR a.id=?) ORDER BY a.due_at,a.id LIMIT 1",
                (at, at, at, identity, identity),
            ).fetchone()
            if row is None:
                return None
            c.execute(
                "UPDATE learning_actions SET status='running',attempts=attempts+1 "
                "WHERE id=?",
                (row["id"],),
            )
            return dict(row)

        return self.database.run_transaction(claim)

    def complete(self, identity, result, *, at, generation_after=None):
        at = require_aware(at)

        def save(c):
            row = c.execute(
                "SELECT status FROM learning_actions WHERE id=?", (identity,)
            ).fetchone()
            if row is None or row[0] != "running":
                raise ValueError("No running action exists")
            if generation_after is not None:
                c.execute(
                    "UPDATE learner_state SET generation_after=? WHERE id='learner'",
                    (require_aware(generation_after),),
                )
            if isinstance(result, Deferred):
                c.execute(
                    "UPDATE learning_actions SET status='waiting',due_at=?,"
                    "result_event=? "
                    "WHERE id=?",
                    (result.at, encode_event(result.event), identity),
                )
                return
            if result is not None:
                if not isinstance(result, Event):
                    raise TypeError(
                        "Action handlers must return Event, Deferred, or None"
                    )
                self._dispatch(c, result)
            c.execute(
                "UPDATE learning_actions SET status='completed',completed_at=? "
                "WHERE id=?",
                (at, identity),
            )

        self.database.run_transaction(save)

    def failure(self, row, error, *, at, delay_hours=None, pause=False):
        at = require_aware(at)

        def save(c):
            if delay_hours is None:
                c.execute(
                    "UPDATE learning_actions SET status='failed',error=? WHERE id=?",
                    (str(error), row["id"]),
                )
            else:
                due = add_elapsed(at, hours=delay_hours)
                c.execute(
                    "UPDATE learning_actions SET status='pending',error=?,due_at=? "
                    "WHERE id=?",
                    (str(error), due, row["id"]),
                )
                if pause:
                    c.execute(
                        "UPDATE learner_state SET paused_until=? WHERE id='learner'",
                        (due,),
                    )

        self.database.run_transaction(save)

    def recover_interrupted(self):
        """Call only at exclusive startup, never beside a live worker."""
        return self.database.run_transaction(
            lambda c: (
                c.execute(
                    "UPDATE learning_actions SET status='uncertain' "
                    "WHERE status='running'"
                ).rowcount
            )
        )


class ActionRunner:
    def __init__(self, store, handlers, *, alert=None, generation_delay=None):
        self.store, self.handlers, self.alert = store, handlers, alert
        self.generation_delay = generation_delay

    async def dispatch(self, event):
        with structlog.contextvars.bound_contextvars(trace_id=event.trace_id):
            return await asyncio.to_thread(self.store.dispatch, event)

    async def run_once(self, *, at, identity=None):
        at = require_aware(at)
        row = await asyncio.to_thread(self.store.claim, at, identity)
        if row is None:
            return "idle"
        values = json.loads(row["action_json"])
        values["article_ids"] = tuple(values.get("article_ids", ()))
        action = Action(**values)
        with structlog.contextvars.bound_contextvars(
            trace_id=action.trace_id, action_id=row["id"]
        ):
            try:
                if row["status"] == "waiting":
                    result = decode_event(row["result_event"])
                else:
                    if action.kind not in self.handlers:
                        raise ValueError(f"TODO(ACTION-HANDLER): missing {action.kind}")
                    result = await self.handlers[action.kind](action, at)
                after = (
                    self.generation_delay(at)
                    if self.generation_delay and action.kind in GENERATION_ACTIONS
                    else None
                )
                await asyncio.to_thread(
                    self.store.complete,
                    row["id"],
                    result,
                    at=at,
                    generation_after=after,
                )
                log.info("action_completed", kind=action.kind)
                return "completed"
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                retryable = not isinstance(
                    error, httpx.HTTPStatusError
                ) or error.response.status_code in {429, 500, 502, 503, 504}
                await asyncio.to_thread(
                    self.store.failure,
                    row,
                    error,
                    at=at,
                    delay_hours=0.25 if retryable else None,
                    pause=retryable,
                )
                log.exception("local_model_paused", kind=action.kind)
                if self.alert:
                    await self.alert(action.trace_id, "local_model_unavailable")
                return "retry" if retryable else "failed"
            except (subprocess.SubprocessError, TimeoutError) as error:
                retry = (
                    action.kind in {"exam", "grade", "select_articles"}
                    and row["attempts"] < 3
                )
                await asyncio.to_thread(
                    self.store.failure,
                    row,
                    error,
                    at=at,
                    delay_hours=6 if retry else None,
                )
                log.exception("curator_deferred", retry=retry)
                if self.alert:
                    await self.alert(action.trace_id, "curator_unavailable")
                return "retry" if retry else "failed"
            except Exception as error:
                await asyncio.to_thread(self.store.failure, row, error, at=at)
                log.exception("action_failed", kind=action.kind)
                raise
