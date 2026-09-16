"""Persist unread messages before work is queued; expose only delivered replies."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import structlog

from src.core.time_utils import now, require_aware
from src.publish import Destination

log = structlog.get_logger("blogai.chat_inbox")


class ChatInbox:
    def __init__(self, gateway, providers, *, clock=now):
        self.gateway, self.providers, self.clock = gateway, providers, clock
        self.database = gateway.service.database
        self.running = {}

    async def accept(self, channel, text, trace_id, *, received_at=None):
        at = require_aware(received_at or self.clock())

        def save(c):
            session = c.execute(
                "SELECT id FROM sessions WHERE channel=? AND closed_at IS NULL",
                (channel,),
            ).fetchone()
            if session is None:
                enabled = c.execute(
                    "SELECT value FROM life_state WHERE key=?",
                    ("chat.enabled:" + channel,),
                ).fetchone()
                if enabled is None or not json.loads(enabled[0]):
                    return False
                session_id = uuid4().hex
                c.execute(
                    "INSERT INTO "
                    "sessions(id,channel,opened_at,turns,tokens_used,mood_start) "
                    "VALUES (?,?,?,0,0,'')",
                    (session_id, channel, self.clock()),
                )
                session = (session_id,)
            prior = c.execute(
                "SELECT question,channel FROM chat_inbox WHERE id=?", (trace_id,)
            ).fetchone()
            if prior and tuple(prior) != (text, channel):
                raise ValueError("An incoming message identity cannot change")
            c.execute(
                "INSERT OR IGNORE INTO "
                "chat_inbox(id,session_id,channel,question,received_at,"
                "deferred_reason) "
                "VALUES "
                "(?,?,?,?,?,?)",
                (
                    trace_id,
                    session[0],
                    channel,
                    text,
                    at,
                    "sleep"
                    if c.execute(
                        "SELECT 1 FROM sleep_log WHERE actual_bedtime<=? AND wake_at>?",
                        (at, at),
                    ).fetchone()
                    else None,
                ),
            )
            return True

        return await asyncio.to_thread(self.database.run_transaction, save)

    def recover(self):
        def save(c):
            for row in c.execute(
                "SELECT channel FROM sessions WHERE closed_at IS NULL"
            ).fetchall():
                c.execute(
                    "INSERT OR IGNORE INTO life_state VALUES (?, 'true', ?)",
                    ("chat.enabled:" + row[0], self.clock()),
                )
            # A ready reply is bound to outbox and must await its receipt.
            for row in c.execute(
                "SELECT id,attempts FROM chat_inbox WHERE status='generating'"
            ).fetchall():
                trace = f"{row['id']}:reply:{row['attempts']}"
                bound = c.execute(
                    "SELECT 1 FROM chat_replies WHERE trace_id=? AND outbox_id IS NOT "
                    "NULL",
                    (trace,),
                ).fetchone()
                c.execute(
                    "UPDATE chat_inbox SET status=? WHERE id=?",
                    ("ready" if bound else "pending", row["id"]),
                )

        self.database.run_transaction(save)

    def _next(self, channel, at):
        with self.database.connection(readonly=True) as c:
            row = c.execute(
                "SELECT i.*,s.closed_at FROM chat_inbox i JOIN sessions s ON "
                "s.id=i.session_id "
                "WHERE i.channel=? AND i.status IN ('pending','ready') "
                "AND (i.next_try_at IS NULL OR i.next_try_at<=?) ORDER BY "
                "i.received_at,i.id LIMIT 1",
                (channel, at),
            ).fetchone()
        return dict(row) if row else None

    async def tick(self):
        at = self.clock()
        blocks = await self.providers.context(at)
        for channel in ("dm", "topic"):
            if channel in self.running and not self.running[channel].done():
                continue
            row = await asyncio.to_thread(self._next, channel, at)
            if row is None:
                continue
            if row["closed_at"]:
                await self._status(row["id"], "cancelled")
                continue
            if row["status"] == "ready":
                await asyncio.to_thread(self._receipt, row)
                continue
            if not blocks["day"].chat_allowed:
                await asyncio.to_thread(
                    self.database.run_transaction,
                    lambda c, identity=row["id"]: c.execute(
                        "UPDATE chat_inbox SET deferred_reason='sleep' WHERE id=?",
                        (identity,),
                    ),
                )
                continue
            self.running[channel] = asyncio.create_task(self._answer(row))

    async def _status(self, identity, status):
        await asyncio.to_thread(
            self.database.run_transaction,
            lambda c: c.execute(
                "UPDATE chat_inbox SET status=? WHERE id=?", (status, identity)
            ),
        )

    def _receipt(self, row):
        trace = f"{row['id']}:reply:{row['attempts']}"

        def save(c):
            reply = c.execute(
                "SELECT r.sent_at,o.payload FROM chat_replies r LEFT JOIN outbox o ON "
                "o.id=r.outbox_id WHERE r.trace_id=?",
                (trace,),
            ).fetchone()
            if reply and reply["sent_at"]:
                c.execute(
                    "UPDATE chat_inbox SET status='delivered' WHERE id=?", (row["id"],)
                )
            elif (
                reply
                and reply["payload"]
                and json.loads(reply["payload"]).get("cancelled_reason")
            ):
                c.execute(
                    "UPDATE chat_inbox SET status='pending',next_try_at=NULL WHERE "
                    "id=?",
                    (row["id"],),
                )

        self.database.run_transaction(save)

    async def _answer(self, row):
        at = self.clock()
        attempt = row["attempts"] + 1
        trace = f"{row['id']}:reply:{attempt}"
        with structlog.contextvars.bound_contextvars(trace_id=trace):
            try:
                blocks = await self.gateway.context_provider()
                if not blocks["day"].chat_allowed:
                    return
                await asyncio.to_thread(
                    self.database.run_transaction,
                    lambda c: c.execute(
                        "UPDATE chat_inbox SET "
                        "status='generating',seen_at=coalesce(seen_at,?),attempts=? "
                        "WHERE id=?",
                        (at, attempt, row["id"]),
                    ),
                )
                result = await self.gateway.service.reply(
                    row["channel"],
                    row["question"],
                    trace_id=trace,
                    incoming_trace_id=row["id"],
                    delivery_context={
                        "received_at": row["received_at"],
                        "seen_at": at.isoformat(),
                        "deferred_reason": row["deferred_reason"],
                    },
                    **blocks,
                )
                if result is None:
                    await self._status(row["id"], "cancelled")
                    return
                fresh = (await self.providers.context(self.clock()))["day"]
                if (
                    not fresh.chat_allowed
                    or fresh.activity_id != blocks["day"].activity_id
                ):
                    await self._status(row["id"], "pending")
                    return
                await asyncio.to_thread(
                    self.database.run_transaction,
                    lambda c: c.execute(
                        "UPDATE chat_replies SET activity_id=?,valid_until=? WHERE "
                        "trace_id=?",
                        (fresh.activity_id, fresh.activity_until, trace),
                    ),
                )
                destination = (
                    Destination("chat-dm", "mika", self.gateway.layout.owner_id)
                    if row["channel"] == "dm"
                    else self.gateway.layout.destination("chat")
                )
                await asyncio.to_thread(
                    self.gateway.publisher.enqueue_operation,
                    trace + ":chat",
                    destination,
                    trace_id=trace,
                    method="message",
                    text=result.text,
                    chat_reply=trace,
                )
                await self._status(row["id"], "ready")
            except Exception as error:
                error_type = type(error).__name__
                log.exception("chat_reply_deferred", error_type=type(error).__name__)

                def recover_delivery(c):
                    bound = c.execute(
                        "SELECT 1 FROM chat_replies WHERE trace_id=? "
                        "AND outbox_id IS NOT NULL",
                        (trace,),
                    ).fetchone()
                    c.execute(
                        "UPDATE chat_inbox SET status=?,error=?,next_try_at=? "
                        "WHERE id=?",
                        (
                            "ready"
                            if bound
                            else "rejected"
                            if attempt >= 3
                            else "pending",
                            error_type,
                            None if bound else self.clock() + timedelta(minutes=5),
                            row["id"],
                        ),
                    )

                await asyncio.to_thread(self.database.run_transaction, recover_delivery)

    async def close(self):
        await asyncio.gather(*self.running.values())
