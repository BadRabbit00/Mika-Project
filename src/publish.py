"""Transactional delivery intents with conservative crash recovery.

An unsent row with a non-NULL next_try_at is pending. Claiming it atomically
clears next_try_at before touching the network. An interrupted claim remains
uncertain until a verified Telegram receipt is supplied; it is never resent
automatically. Telegram cannot provide an exactly-once transaction with SQLite.
"""

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import structlog

from src.core.db import enqueue_outbox
from src.core.time_utils import now, require_aware, to_utc_iso

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
        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            return self.database.run_transaction(
                lambda c: enqueue_outbox(
                    c,
                    post_id=key,
                    channel=destination.channel,
                    payload=payload,
                    next_try_at=at,
                )
            )


class OutboxWorker:
    def __init__(self, database, transport):
        self.database, self.transport = database, transport

    def _claim(self, at):
        def claim(connection):
            row = connection.execute(
                "SELECT * FROM outbox WHERE sent_at IS NULL AND tg_message_id IS NULL "
                "AND next_try_at<=? ORDER BY next_try_at, id LIMIT 1",
                (to_utc_iso(at),),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE outbox SET attempts=attempts+1, next_try_at=NULL WHERE id=?",
                (row["id"],),
            )
            return dict(row)

        return self.database.run_transaction(claim)

    def uncertain(self):
        with self.database.connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM outbox WHERE sent_at IS NULL "
                    "AND next_try_at IS NULL AND attempts>0 ORDER BY id"
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
            log.info(
                "outbox_receipt_saved",
                outbox_id=outbox_id,
                message_id=message_id,
                trace_id=payload["trace_id"],
            )

        self.database.run_transaction(finish)

    async def run_once(self, *, at: datetime | None = None) -> str:
        at = require_aware(now() if at is None else at)
        row = await asyncio.to_thread(self._claim, at)
        if row is None:
            return "idle"
        payload = json.loads(row["payload"])
        with structlog.contextvars.bound_contextvars(trace_id=payload["trace_id"]):
            try:
                message_id = await self.transport.send(payload)
            except DeliveryRejected as error:
                log.warning(
                    "outbox_remote_rejection", outbox_id=row["id"], error=str(error)
                )
                if error.retry_after is not None:
                    retry_at = at + timedelta(seconds=error.retry_after)
                    await asyncio.to_thread(
                        self.database.run_transaction,
                        lambda c: c.execute(
                            "UPDATE outbox SET next_try_at=? WHERE id=? "
                            "AND sent_at IS NULL",
                            (retry_at, row["id"]),
                        ),
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
