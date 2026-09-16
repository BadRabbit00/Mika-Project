"""Durable activity changes and publication obligations independent of quotas."""

import hashlib
import json
from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4

import structlog
from ruamel.yaml import YAML

from src.core.itinerary import Activity
from src.core.life_engine import packed
from src.core.time_utils import from_utc_iso, require_aware, to_utc_iso

log = structlog.get_logger("blogai.transitions")


def activity_data(activity):
    return {
        key: to_utc_iso(value) if key in {"starts_at", "ends_at"} else value
        for key, value in asdict(activity).items()
    } | {"kind": "household_task" if activity.task_id else activity.kind}


def same_action(left, right):
    return left and all(
        left.get(key) == right.get(key)
        for key in ("kind", "location", "origin", "destination", "task_id", "subject")
    )


class TransitionJournal:
    def __init__(self, providers):
        self.providers, self.database = providers, providers.life.database
        self.config = YAML(typ="safe").load(
            providers.config_dir / "activity_transitions.yaml"
        )

    def _record(self, c, kind, previous, current, at, reason, *, identity=None):
        current_data = activity_data(current)
        identity = (
            identity
            or "transition:"
            + hashlib.sha256(
                packed(
                    [kind, previous.get("id") if previous else None, current.id]
                ).encode()
            ).hexdigest()[:24]
        )
        payload = {
            "kind": kind,
            "at": to_utc_iso(at),
            "reason": reason,
            "reason_text": self.config["reasons"][reason],
            "facts": self.config["facts"][kind],
            "previous": previous,
            "current": current_data,
        }
        inserted = c.execute(
            "INSERT OR IGNORE INTO "
            "activity_transitions(id,at,kind,from_activity_id,to_activity_id,payload) "
            "VALUES (?,?,?,?,?,?)",
            (
                identity,
                at,
                kind,
                previous.get("id") if previous else None,
                current.id,
                packed(payload),
            ),
        )
        if inserted.rowcount:
            log.info(
                "activity_transition_recorded",
                trace_id=identity,
                kind=kind,
                reason=reason,
            )

    def observe(self, at, *, reason=None):
        at = require_aware(at)
        current = self.providers.itinerary.current(at)

        def save(c):
            row = c.execute(
                "SELECT value FROM life_state WHERE key='life.transition_cursor'"
            ).fetchone()
            cursor = json.loads(row[0]) if row else None
            previous = cursor["activity"] if cursor else None
            if cursor and at < from_utc_iso(cursor["at"]):
                raise ValueError("Activity observations cannot move backwards")
            candidates = [current]
            if cursor:
                candidates = [
                    Activity.from_row(item)
                    for item in c.execute(
                        "SELECT * FROM life_activities WHERE state='active' AND "
                        "starts_at>? "
                        "AND starts_at<=? ORDER BY starts_at,id",
                        (cursor["at"], at),
                    )
                ]
                if not candidates or candidates[-1].id != current.id:
                    candidates.append(current)
            for item in candidates:
                data = activity_data(item)
                if same_action(previous, data):
                    previous = data
                    continue
                when = max(from_utc_iso(cursor["at"]), item.starts_at) if cursor else at
                when = min(at, when)
                active_break = c.execute(
                    "SELECT * FROM life_breaks WHERE starts_at<=? AND ends_at>? ORDER "
                    "BY starts_at DESC LIMIT 1",
                    (when, when),
                ).fetchone()
                was_studying = (
                    previous
                    and previous["kind"] == "study"
                    and previous.get("task_id") is None
                )
                if was_studying and not item.can_study:
                    future = c.execute(
                        "SELECT 1 FROM life_activities WHERE day=? AND state='active' "
                        "AND can_study=1 AND task_id IS NULL AND ends_at>? LIMIT 1",
                        (item.day, when),
                    ).fetchone()
                    observed_reason = (
                        reason if when == at and item.id == current.id else None
                    )
                    why = observed_reason or (
                        active_break["reason"]
                        if active_break
                        else "bedtime"
                        if item.kind == "wind_down"
                        else "meal"
                        if item.kind in {"lunch", "dinner", "breakfast"}
                        else "next_activity"
                        if future
                        else "scheduled_end"
                    )
                    self._record(
                        c,
                        "pause_study" if future else "finish_study",
                        previous,
                        item,
                        when,
                        why,
                    )
                if item.can_study and not was_studying:
                    earlier = c.execute(
                        "SELECT 1 FROM activity_transitions WHERE kind IN "
                        "('start_study','resume_study') "
                        "AND at>=? AND at<=? LIMIT 1",
                        (at.replace(hour=0, minute=0, second=0, microsecond=0), at),
                    ).fetchone()
                    self._record(
                        c,
                        "resume_study" if earlier else "start_study",
                        previous,
                        item,
                        when,
                        "resume" if earlier else "next_activity",
                    )
                if item.kind == "travel":
                    self._record(c, "depart", previous, item, when, "departure")
                elif previous and previous["kind"] == "travel":
                    self._record(
                        c,
                        "home" if item.location == "дом" else "arrive",
                        previous,
                        item,
                        when,
                        "arrival",
                    )
                if item.kind == "wind_down":
                    self._record(c, "wind_down", previous, item, when, "bedtime")
                previous = data
            for pause in c.execute(
                "SELECT * FROM life_breaks WHERE status='planned' AND starts_at<=?",
                (at,),
            ).fetchall():
                returned = c.execute(
                    "SELECT * FROM life_activities WHERE starts_at<=? AND ends_at>? "
                    "AND state='active' AND can_study=1",
                    (pause["ends_at"], pause["ends_at"]),
                ).fetchone()
                resumed = returned is not None
                if resumed and at < from_utc_iso(pause["ends_at"]):
                    continue
                c.execute(
                    "UPDATE life_breaks SET status=?,return_activity_id=? WHERE id=?",
                    (
                        "resumed" if resumed else "cancelled",
                        returned["id"] if resumed else pause["return_activity_id"],
                        pause["id"],
                    ),
                )
                if not resumed:
                    why = reason or (
                        "health"
                        if self.providers.life._state(c)["ill_until"]
                        and from_utc_iso(self.providers.life._state(c)["ill_until"])
                        > at
                        else "bedtime"
                        if current.kind in {"wind_down", "sleep"}
                        else "next_activity"
                    )
                    self._record(
                        c,
                        "finish_study",
                        previous,
                        current,
                        at,
                        why,
                        identity=pause["id"] + ":cancel-return",
                    )
            c.execute(
                "INSERT INTO life_state VALUES ('life.transition_cursor',?,?) ON "
                "CONFLICT(key) "
                "DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (
                    packed({"at": to_utc_iso(at), "activity": activity_data(current)}),
                    at,
                ),
            )

        self.database.run_transaction(save)

    def evidence(self, at, *, c=None):
        if c is None:
            with self.database.connection(readonly=True) as connection:
                return self.evidence(at, c=connection)
        current = self.providers.itinerary.current(at)
        planned = [
            activity_data(Activity.from_row(row))
            for row in c.execute(
                "SELECT * FROM life_activities WHERE state='active' AND starts_at>=? "
                "AND starts_at<? ORDER BY starts_at LIMIT 6",
                (at, at + timedelta(hours=2)),
            )
        ]
        return {"current": activity_data(current), "planned": planned}

    def _release(self, c, event, at):
        if event["post_id"]:
            rows = c.execute(
                "SELECT * FROM outbox WHERE json_extract(payload,'$.post_id')=?",
                (event["post_id"],),
            ).fetchall()
            if any(
                row["sent_at"]
                or (
                    row["attempts"] > 0
                    and row["next_try_at"] is None
                    and not json.loads(row["payload"]).get("cancelled_reason")
                )
                for row in rows
            ):
                return False
            for row in rows:
                payload = json.loads(row["payload"]) | {
                    "cancelled_reason": "activity_changed"
                }
                c.execute(
                    "UPDATE outbox SET payload=?,next_try_at=NULL WHERE id=?",
                    (packed(payload), row["id"]),
                )
        c.execute(
            "UPDATE activity_transitions SET intent_id=NULL,post_id=NULL,retry_at=? "
            "WHERE intent_id=? AND delivered_at IS NULL",
            (
                event["retry_at"] if event["publication_status"] == "killed" else at,
                event["id"],
            ),
        )
        return True

    def next(self, at):
        at = require_aware(at)
        current = self.providers.itinerary.current(at)
        if not current.can_publish:
            return None

        def save(c):
            intents = c.execute(
                "SELECT DISTINCT e.* FROM activity_transitions t JOIN life_events e "
                "ON e.id=t.intent_id "
                "WHERE t.delivered_at IS NULL ORDER BY e.at"
            ).fetchall()
            for intent in intents:
                if intent["publication_status"] in {"expired", "killed"}:
                    if not self._release(c, intent, at):
                        continue
                elif intent["publication_status"] in {"pending", "draft"}:
                    if intent["retry_at"] and at < from_utc_iso(intent["retry_at"]):
                        continue
                    return dict(intent)
            pending = c.execute(
                "SELECT * FROM activity_transitions WHERE delivered_at IS NULL AND "
                "intent_id IS NULL "
                "AND (retry_at IS NULL OR retry_at<=?) ORDER BY at,rowid LIMIT ?",
                (at, self.config["max_transitions_per_post"]),
            ).fetchall()
            if current.kind == "wind_down":
                sleep_notice = c.execute(
                    "SELECT * FROM activity_transitions WHERE to_activity_id=? "
                    "AND kind='wind_down' AND delivered_at IS NULL AND intent_id IS "
                    "NULL AND (retry_at IS NULL OR retry_at<=?)",
                    (current.id, at),
                ).fetchone()
                if sleep_notice and all(
                    row["id"] != sleep_notice["id"] for row in pending
                ):
                    pending = pending[: self.config["max_transitions_per_post"] - 1] + [
                        sleep_notice
                    ]
            if not pending:
                return None
            identity = "transition-post:" + uuid4().hex
            evidence = self.evidence(at, c=c)
            payload = evidence | {
                "facts": " ".join(
                    json.loads(row["payload"])["facts"] for row in pending
                ),
                "mandatory": True,
                "transitions": [
                    json.loads(row["payload"]) | {"id": row["id"]} for row in pending
                ],
            }
            related = c.execute(
                "SELECT * FROM life_events WHERE activity_id=? AND "
                "publication_status='pending' "
                "AND kind!='transition' AND at<=? AND NOT EXISTS (SELECT 1 "
                "FROM life_events intent JOIN activity_transitions notice "
                "ON notice.intent_id=intent.id WHERE notice.delivered_at IS NULL "
                "AND json_extract(intent.payload,'$.related_event_id')=life_events.id) "
                "ORDER BY at DESC LIMIT 1",
                (current.id, at),
            ).fetchone()
            if related:
                payload["related_event"] = json.loads(related["payload"])
                payload["related_event_id"] = related["id"]
            c.execute(
                "INSERT INTO "
                "life_events(id,entity,at,kind,payload,activity_id,valid_until) "
                "VALUES (?,?,?,'transition',?,?,?)",
                (identity, identity, at, packed(payload), current.id, current.ends_at),
            )
            c.executemany(
                "UPDATE activity_transitions SET intent_id=? WHERE id=?",
                [(identity, row["id"]) for row in pending],
            )
            return dict(
                c.execute(
                    "SELECT * FROM life_events WHERE id=?", (identity,)
                ).fetchone()
            )

        return self.database.run_transaction(save)

    def bind(self, event_id, post_id):
        self.database.run_transaction(
            lambda c: c.execute(
                "UPDATE activity_transitions SET post_id=? WHERE intent_id=? AND "
                "delivered_at IS NULL",
                (post_id, event_id),
            )
        )
