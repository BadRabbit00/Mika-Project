"""Meaningful world diffs and one delivery-confirmed, pinned current-state message."""

import json
from dataclasses import asdict

from src.core.db import enqueue_outbox
from src.core.time_utils import from_utc_iso, require_aware


def differences(before, after, prefix=""):
    changes = {}
    for key in sorted(before.keys() | after.keys()):
        left, right = before.get(key), after.get(key)
        if left == right:
            continue
        name = prefix + key
        if isinstance(left, dict) and isinstance(right, dict):
            changes.update(differences(left, right, name + "."))
        else:
            changes[name] = [left, right]
    return changes


class WorldJournal:
    def __init__(self, database, *, log_destination=None, state_destination=None):
        self.database = database
        self.log_destination, self.state_destination = (
            log_destination,
            state_destination,
        )

    def observe(self, snapshot, at, *, cause):
        at = require_aware(at)

        def save(c):
            row = c.execute(
                "SELECT snapshot FROM world_changes ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous = json.loads(row[0]) if row else {}
            changes = differences(previous, snapshot)
            if not changes:
                return False
            c.execute(
                "INSERT INTO world_changes(at,cause,snapshot,changes) VALUES (?,?,?,?)",
                (
                    at,
                    cause,
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                    json.dumps(changes, ensure_ascii=False, sort_keys=True),
                ),
            )
            return True

        return self.database.run_transaction(save)

    @staticmethod
    def _text(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def _enqueue(self, c, key, destination, method, at, **data):
        return enqueue_outbox(
            c,
            post_id=key,
            channel=destination.channel,
            next_try_at=at,
            payload=data
            | {
                "method": method,
                "destination": asdict(destination),
                "post_id": None,
                "trace_id": key,
                "ops_mirror": True,
            },
        )

    def flush(self, at):
        """Keep one state operation in flight; uncertain sends block replacement."""
        at = require_aware(at)

        def save(c):
            if self.log_destination:
                for row in c.execute(
                    "SELECT * FROM world_changes WHERE outbox_id IS NULL "
                    "ORDER BY sequence LIMIT 10"
                ).fetchall():
                    lines = [
                        "World change",
                        "at: " + row["at"],
                        "cause: " + row["cause"],
                    ]
                    for key, (before, after) in json.loads(row["changes"]).items():
                        lines.append(
                            f"{key}: {self._text(before)} -> {self._text(after)}"
                        )
                    text = "\n".join(lines)
                    if len(text.encode("utf-16-le")) // 2 <= 3900:
                        identity = self._enqueue(
                            c,
                            f"world-change:{row['sequence']}",
                            self.log_destination,
                            "message",
                            at,
                            text=text,
                        )
                    else:
                        identity = self._enqueue(
                            c,
                            f"world-change:{row['sequence']}",
                            self.log_destination,
                            "document",
                            at,
                            filename=f"world-change-{row['sequence']}.json",
                            content=row["changes"],
                            caption="\n".join(lines[:3]),
                        )
                    c.execute(
                        "UPDATE world_changes SET outbox_id=? WHERE sequence=?",
                        (identity, row["sequence"]),
                    )
            if self.state_destination is None:
                return
            latest = c.execute(
                "SELECT * FROM world_changes ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if latest is None:
                return
            target = self.state_destination
            previous = c.execute(
                "SELECT * FROM outbox WHERE json_extract(payload,'$.world_state')=1 "
                "AND json_extract(payload,'$.destination.chat_id')=? "
                "AND json_extract(payload,'$.destination.topic_id')=? "
                "ORDER BY id DESC LIMIT 1",
                (target.chat_id, target.topic_id),
            ).fetchone()
            if previous and previous["sent_at"] is None:
                return
            payload = json.loads(previous["payload"]) if previous else {}
            if previous:
                pin_key = f"world-pin:{target.chat_id}:{target.topic_id}"
                if not c.execute(
                    "SELECT 1 FROM outbox WHERE json_extract(payload,'$.trace_id')=?",
                    (pin_key,),
                ).fetchone():
                    self._enqueue(
                        c,
                        pin_key,
                        target,
                        "pin",
                        at,
                        message_id=previous["tg_message_id"],
                        depends_on=previous["id"],
                    )
                if payload.get("world_sequence") == latest["sequence"]:
                    return
            content = json.dumps(
                {"observed_at": latest["at"], **json.loads(latest["snapshot"])},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            snapshot = json.loads(latest["snapshot"])
            current = snapshot.get("current", {})
            caption = "Mika state\nUpdated (Asia/Almaty): " + from_utc_iso(
                latest["at"]
            ).strftime("%Y-%m-%d %H:%M")
            for key in (
                "location",
                "activity",
                "ongoing",
                "until",
                "mood",
                "health",
                "productivity",
                "cash",
                "savings",
                "debt",
            ):
                value = current.get(key, snapshot.get(key))
                if value is not None:
                    if key == "ongoing":
                        value = value["label"]
                    elif key == "until":
                        value = from_utc_iso(value).strftime("%m-%d %H:%M")
                    caption += "\n" + key + ": " + str(value)
            caption += "\nThe attached JSON contains the complete current state."
            data = dict(
                filename="mika-state.json",
                content=content,
                caption=caption[:900],
                world_state=True,
                world_sequence=latest["sequence"],
            )
            if previous:
                data["message_id"] = previous["tg_message_id"]
            self._enqueue(
                c,
                f"world-state:{target.chat_id}:{target.topic_id}:{latest['sequence']}",
                target,
                "edit_document" if previous else "document",
                at,
                **data,
            )

        self.database.run_transaction(save)
