"""Transactional delivery intents with conservative crash recovery.

An unsent row with a non-NULL next_try_at is pending. Claiming it atomically
clears next_try_at before touching the network. An interrupted claim remains
uncertain until a verified Telegram receipt is supplied; it is never resent
automatically. Telegram cannot provide an exactly-once transaction with SQLite.
"""

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime

import structlog

from src.core.chat_store import SessionStore
from src.core.db import enqueue_outbox
from src.core.time_utils import add_elapsed, now, require_aware, to_utc_iso

log = structlog.get_logger("blogai.publish")


@dataclass(frozen=True)
class Destination:
    channel: str
    bot: str
    chat_id: int
    topic_id: int | None = None
    primary: bool = False

    def __post_init__(self):
        if not self.channel.strip() or self.bot not in {"mika", "curator", "ops"}:
            raise ValueError("A named destination and known bot are required")
        if type(self.chat_id) is not int or self.chat_id == 0:
            raise ValueError("A Telegram chat ID is required")
        if self.topic_id is not None and (
            type(self.topic_id) is not int or self.topic_id <= 0
        ):
            raise ValueError("Topic IDs must be positive integers")


class DeliveryRejected(Exception):
    """The remote API explicitly confirms it did not accept the operation."""

    def __init__(self, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        if retry_after is not None and (
            type(retry_after) is not int or retry_after < 0
        ):
            raise ValueError("Retry delay must be a nonnegative integer")
        self.retry_after = retry_after


class Publisher:
    def __init__(self, database):
        self.database = database

    def enqueue_post(
        self,
        post_id: str,
        destinations: list[Destination],
        *,
        trace_id: str,
        at: datetime | None = None,
        markup: dict | None = None,
    ) -> list[int]:
        at = require_aware(now() if at is None else at)
        if (
            not trace_id
            or not destinations
            or sum(d.primary for d in destinations) != 1
        ):
            raise ValueError("A trace and exactly one primary destination are required")
        if len({d.channel for d in destinations}) != len(destinations):
            raise ValueError("Publication destinations must be distinct")

        def save(connection):
            post = connection.execute(
                "SELECT * FROM posts WHERE id=?", (post_id,)
            ).fetchone()
            if (
                post is None
                or post["state"] not in {"draft", "queued", "published"}
                or not post["text"]
            ):
                raise ValueError("Post is not a publishable draft")
            if connection.execute(
                "SELECT 1 FROM invalidated WHERE post_id=?", (post_id,)
            ).fetchone():
                raise ValueError("Invalidated posts are not publishable")
            ids = []
            for destination in destinations:
                payload = {
                    "method": "message",
                    "destination": asdict(destination),
                    "post_id": post_id,
                    "trace_id": trace_id,
                    "text": post["text"],
                    "parse_mode": "HTML",
                    "reply_markup": markup if destination.primary else None,
                }
                ids.append(
                    enqueue_outbox(
                        connection,
                        post_id=post_id,
                        channel=destination.channel,
                        payload=payload,
                        next_try_at=at,
                    )
                )
            connection.execute(
                "UPDATE posts SET state='queued' WHERE id=? AND state='draft'",
                (post_id,),
            )
            return ids

        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            return self.database.run_transaction(save)

    def enqueue_operation(
        self,
        key: str,
        destination: Destination,
        *,
        trace_id: str,
        method: str,
        at: datetime | None = None,
        **data,
    ) -> int:
        """Persist operational messages/documents/edits with an explicit stable key."""
        return self.enqueue_operations(
            [
                dict(
                    key=key,
                    destination=destination,
                    trace_id=trace_id,
                    method=method,
                    at=at,
                    **data,
                )
            ]
        )[0]

    @staticmethod
    def _operation(key, destination, *, trace_id, method, at=None, **data):
        at = require_aware(now() if at is None else at)
        if method not in {"message", "document", "edit", "pin"} or not trace_id:
            raise ValueError("Unsupported Telegram operation or missing trace")
        if {"method", "destination", "trace_id", "post_id"} & data.keys():
            raise ValueError("Reserved delivery fields cannot be overridden")
        payload = data | {
            "method": method,
            "destination": asdict(destination),
            "trace_id": trace_id,
            "post_id": None,
        }
        return key, destination.channel, payload, at

    def enqueue_operations(self, operations: list[dict]) -> list[int]:
        """Commit a batch atomically; stable keys make uncertain commits retryable."""
        prepared = [self._operation(**operation) for operation in operations]

        def save(connection):
            ids = []
            for key, channel, payload, at in prepared:
                with structlog.contextvars.bound_contextvars(
                    trace_id=payload["trace_id"]
                ):
                    ids.append(
                        enqueue_outbox(
                            connection,
                            post_id=key,
                            channel=channel,
                            payload=payload,
                            next_try_at=at,
                        )
                    )
                    if payload.get("chat_reply"):
                        SessionStore.bind_delivery(
                            connection, payload["chat_reply"], payload, ids[-1]
                        )
            return ids

        if not prepared:
            return []
        with structlog.contextvars.bound_contextvars(
            trace_id=prepared[0][2]["trace_id"]
        ):
            return self.database.run_transaction(save)


class DeliveryExpired(Exception):
    """The text describes an activity that ended before delivery."""


class OutboxWorker:
    def __init__(self, database, transport, *, allowed=None, clock=now):
        self.database, self.transport = database, transport
        self.allowed = allowed
        self.clock = clock

    @staticmethod
    def _scopes(payload):
        destination = payload["destination"]
        return "bot:" + destination["bot"], "chat:" + str(destination["chat_id"])

    @staticmethod
    def _defer_scope(connection, scope, until):
        connection.execute(
            "INSERT INTO telegram_delivery_limits(scope,next_at) VALUES (?,?) "
            "ON CONFLICT(scope) DO UPDATE SET next_at=max(next_at,excluded.next_at)",
            (scope, until),
        )

    @staticmethod
    def _due(connection, at):
        return connection.execute(
            "SELECT o.* FROM outbox o "
            "LEFT JOIN telegram_delivery_limits b ON b.scope="
            "'bot:'||json_extract(o.payload,'$.destination.bot') "
            "LEFT JOIN telegram_delivery_limits d ON d.scope="
            "'chat:'||json_extract(o.payload,'$.destination.chat_id') "
            "WHERE o.sent_at IS NULL "
            "AND o.tg_message_id IS NULL AND o.next_try_at<=? "
            "AND (b.next_at IS NULL OR b.next_at<=?) "
            "AND (d.next_at IS NULL OR d.next_at<=?) "
            "AND (json_extract(o.payload, '$.depends_on') IS NULL OR EXISTS "
            "(SELECT 1 FROM outbox parent WHERE parent.id="
            "json_extract(o.payload, '$.depends_on') "
            "AND parent.sent_at IS NOT NULL)) "
            "ORDER BY (o.channel='machine'), o.next_try_at, o.id LIMIT 1",
            (to_utc_iso(at),) * 3,
        ).fetchone()

    def _claim(self, at):
        # Idle polls must not contend with learning or generation writes.
        with self.database.connection(readonly=True) as connection:
            if self._due(connection, at) is None:
                return None

        def claim(connection):
            # Recheck under BEGIN IMMEDIATE; the read above is only a fast path.
            row = self._due(connection, at)
            if row is None:
                return None
            connection.execute(
                "UPDATE outbox SET attempts=attempts+1, next_try_at=NULL WHERE id=?",
                (row["id"],),
            )
            claimed = dict(row)
            payload = json.loads(claimed["payload"])
            bot_scope, chat_scope = self._scopes(payload)
            # Shared group pacing includes every topic and all three identities.
            # Reserve before sending so concurrent workers cannot burst together.
            interval = 3.1 if payload["destination"]["chat_id"] < 0 else 1.05
            self._defer_scope(connection, bot_scope, add_elapsed(at, minutes=0.04 / 60))
            self._defer_scope(
                connection, chat_scope, add_elapsed(at, minutes=interval / 60)
            )
            if payload.get("depends_on") is not None:
                parent = connection.execute(
                    "SELECT tg_message_id FROM outbox WHERE id=?",
                    (payload["depends_on"],),
                ).fetchone()
                payload["message_id"] = parent[0]
                claimed["payload"] = json.dumps(payload)
            return claimed

        return self.database.run_transaction(claim)

    def uncertain(self):
        with self.database.connection(readonly=True) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM outbox WHERE sent_at IS NULL "
                    "AND next_try_at IS NULL AND attempts>0 "
                    "AND json_extract(payload,'$.cancelled_reason') IS NULL ORDER BY id"
                )
            ]

    def reconcile(self, outbox_id: int, *, message_id: int, at: datetime):
        """A caller must verify this receipt with Telegram before reconciliation."""
        at = require_aware(at)
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("A verified positive Telegram message ID is required")

        def finish(connection):
            row = connection.execute(
                "SELECT * FROM outbox WHERE id=?", (outbox_id,)
            ).fetchone()
            if row is None or row["attempts"] == 0:
                raise ValueError("No claimed delivery exists")
            if row["sent_at"] is not None:
                if row["tg_message_id"] != message_id:
                    raise ValueError("Receipt conflicts with an existing delivery")
                return
            payload = json.loads(row["payload"])
            if payload.get("chat_reply"):
                SessionStore(self.database).confirm_reply(
                    connection, payload["chat_reply"], outbox_id=outbox_id, at=at
                )
            connection.execute(
                "UPDATE outbox SET tg_message_id=?, sent_at=?, next_try_at=NULL "
                "WHERE id=?",
                (message_id, at, outbox_id),
            )
            if payload.get("post_id") and payload["destination"]["primary"]:
                connection.execute(
                    "UPDATE posts SET state='published', published_at=?, "
                    "tg_message_id=? WHERE id=?",
                    (at, message_id, payload["post_id"]),
                )
                connection.execute(
                    "UPDATE life_events SET publication_status='published' WHERE "
                    "post_id=?",
                    (payload["post_id"],),
                )
                connection.execute(
                    "UPDATE activity_transitions SET delivered_at=? WHERE post_id=? "
                    "AND delivered_at IS NULL",
                    (at, payload["post_id"]),
                )
                event = connection.execute(
                    "SELECT * FROM life_events WHERE post_id=?", (payload["post_id"],)
                ).fetchone()
                if event is not None and event["journal_id"] is None:
                    parent = connection.execute(
                        "SELECT journal_id FROM life_events WHERE id=?",
                        (event["cause_id"],),
                    ).fetchone()
                    entity = json.dumps(
                        {"slot": event["kind"], "values": {"event_id": event["id"]}},
                        sort_keys=True,
                    )
                    journal_id = connection.execute(
                        "INSERT INTO life_journal(at,slot,entity,text,continues) "
                        "VALUES (?,?,?,?,?)",
                        (
                            at,
                            event["kind"],
                            entity,
                            payload["text"],
                            parent[0] if parent else None,
                        ),
                    ).lastrowid
                    connection.execute(
                        "UPDATE life_events SET journal_id=? WHERE id=?",
                        (journal_id, event["id"]),
                    )
                    related = json.loads(event["payload"]).get("related_event_id")
                    if related:
                        connection.execute(
                            "UPDATE life_events SET "
                            "publication_status='published',post_id=?,journal_id=? "
                            "WHERE id=?",
                            (payload["post_id"], journal_id, related),
                        )
                    connection.execute(
                        "INSERT INTO narrative(at,kind,gist,trace_id,post_id) VALUES "
                        "(?,?,?,?,?)",
                        (
                            at,
                            "daily"
                            if event["kind"] == "daily"
                            else "situation"
                            if event["kind"] == "situation"
                            else "offtop",
                            json.loads(event["payload"])["facts"],
                            event["id"],
                            payload["post_id"],
                        ),
                    )
            if payload["method"] == "pin" and payload.get("defect_post_id"):
                connection.execute(
                    "UPDATE invalidated SET pinned_id=? WHERE post_id=?",
                    (message_id, payload["defect_post_id"]),
                )
            log.info(
                "outbox_receipt_saved",
                outbox_id=outbox_id,
                message_id=message_id,
                trace_id=payload["trace_id"],
            )

        self.database.run_transaction(finish)

    async def run_once(self, *, at: datetime | None = None) -> str:
        explicit_time = at is not None
        at = require_aware(self.clock() if at is None else at)

        def recover():
            with self.database.connection(readonly=True) as connection:
                row = connection.execute(
                    "SELECT id, tg_message_id FROM outbox WHERE sent_at IS NULL "
                    "AND tg_message_id IS NOT NULL AND attempts>0 ORDER BY id LIMIT 1"
                ).fetchone()
            if row is not None:
                self.reconcile(row[0], message_id=row[1], at=at)
                return True
            return False

        if await asyncio.to_thread(recover):
            return "recovered"
        row = await asyncio.to_thread(self._claim, at)
        if row is None:
            return "idle"
        payload = json.loads(row["payload"])
        with structlog.contextvars.bound_contextvars(trace_id=payload["trace_id"]):
            if self.allowed is not None and (
                payload.get("post_id") or payload.get("chat_reply")
            ):
                try:
                    admitted = await self.allowed(payload, at)
                except DeliveryExpired:
                    payload["cancelled_reason"] = "activity_changed"
                    await asyncio.to_thread(
                        self.database.run_transaction,
                        lambda c: c.execute(
                            "UPDATE outbox SET payload=?,next_try_at=NULL WHERE id=?",
                            (json.dumps(payload), row["id"]),
                        ),
                    )
                    log.info("publication_expired", outbox_id=row["id"])
                    return "expired"
                except ValueError:
                    log.exception("publication_observations_unavailable")
                    admitted = False
                if not admitted:
                    await asyncio.to_thread(
                        self.database.run_transaction,
                        lambda c: c.execute(
                            "UPDATE outbox SET next_try_at=? WHERE id=? "
                            "AND sent_at IS NULL",
                            (add_elapsed(at, minutes=1), row["id"]),
                        ),
                    )
                    log.info("publication_deferred", outbox_id=row["id"])
                    return "deferred"
            try:
                message_id = await self.transport.send(payload)
            except DeliveryRejected as error:
                log.warning(
                    "outbox_remote_rejection", outbox_id=row["id"], error=str(error)
                )
                if error.retry_after is not None:
                    rejected_at = at if explicit_time else require_aware(self.clock())
                    retry_at = add_elapsed(rejected_at, minutes=error.retry_after / 60)

                    def postpone(connection):
                        connection.execute(
                            "UPDATE outbox SET next_try_at=? WHERE id=? "
                            "AND sent_at IS NULL",
                            (retry_at, row["id"]),
                        )
                        # A 429 may apply to the bot or the chat, not just this row.
                        for scope in self._scopes(payload):
                            self._defer_scope(connection, scope, retry_at)

                    await asyncio.to_thread(
                        self.database.run_transaction,
                        postpone,
                    )
                    return "retry"
                return "rejected"
            except Exception:
                log.exception("outbox_delivery_uncertain", outbox_id=row["id"])
                return "uncertain"
            # A crash or cancellation before this commit keeps the durable claim.
            await asyncio.to_thread(
                self.reconcile, row["id"], message_id=message_id, at=at
            )
            return "sent"
