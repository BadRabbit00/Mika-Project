"""Mirror event cards to Telegram while JSONL remains the full local record."""

import asyncio
import json
import logging
import queue
import time
from collections import deque
from uuid import uuid4

import structlog

from src.core.time_utils import now


def _units(text):
    return len(text.encode("utf-16-le")) // 2


def _clip(value, limit):
    text = str(value)
    if _units(text) <= limit:
        return text
    return (
        text.encode("utf-16-le")[: (limit - 1) * 2].decode("utf-16-le", errors="ignore")
        + "…"
    )


class OpsMirror(logging.Handler):
    """Flush up to ten events every five seconds, retaining failed batches."""

    def __init__(self, publisher, destination, registry, *, clock=time.monotonic):
        super().__init__()
        self.publisher, self.destination, self.registry = (
            publisher,
            destination,
            registry,
        )
        self.events = queue.SimpleQueue()
        self.clock = clock
        self._buffer = deque()
        self._pending_operations = None
        self._pending_count = 0
        self._draining = asyncio.Lock()

    def emit(self, record):
        if not isinstance(record.msg, dict):
            return
        event = dict(record.msg)
        if event.get("ops_mirror") or event.get("chat_channel") == "dm":
            return
        name = event.get("event", "")
        domain_change = name == "db_row_change" and event.get("table") not in {
            "outbox",
            "runs",
            "telegram_delivery_limits",
            "chat_inbox",
            "life_state",
            "world_calendars",
            "world_runs",
            "world_steps",
            "world_changes",
            "world_plans",
            "world_appointments",
            "life_events",
            "life_tasks",
            "life_effects",
            "life_activities",
            "life_days",
            "life_breaks",
            "activity_transitions",
            "money_ledger",
            "mood",
        }
        if not (
            domain_change
            or record.levelno >= logging.WARNING
            or name.startswith(
                (
                    "local_generation",
                    "curator_",
                    "job_",
                    "owner_command",
                    "state_transition",
                )
            )
        ):
            return
        if name.startswith("outbox_"):
            return
        event["event_id"] = uuid4().hex
        event.setdefault("trace_id", event["event_id"])
        event["level"] = record.levelname.lower()
        self.events.put((self.clock(), event))

    def _options(self):
        return tuple(
            self.registry.get(key)
            for key in ("log.verbosity", "log.attach_prompts", "log.show_thoughts")
        )

    @staticmethod
    def _card(event):
        lines = [
            _clip(event["event"], 128),
            "trace_id: " + _clip(event["trace_id"], 1024),
            "actor: " + _clip(event.get("actor", event.get("logger", "system")), 1024),
            "status: " + _clip(event.get("status", event["level"]), 128),
        ]
        for key in ("table", "operation", "kind", "cost_usd"):
            if key in event:
                lines.append(key + ": " + _clip(event[key], 128))
        return "\n".join(lines) + "\n"

    def _prepare(self, options):
        verbosity, attach_prompts, show_thoughts = options
        selected, cards, consumed = [], [], 0
        for _, event in self._buffer:
            if (
                verbosity == "quiet"
                and event["level"] not in {"error", "critical"}
                and not event["event"].startswith("state_transition")
            ):
                consumed += 1
                continue
            card = self._card(event)
            if _units("\n".join([*cards, card])) > 4064:
                break
            consumed += 1
            selected.append(event)
            cards.append(card)
        self._pending_count = consumed
        self._pending_operations = []
        if not selected:
            return
        batch_id = selected[0]["event_id"]
        common = dict(
            destination=self.destination, trace_id=selected[0]["trace_id"], at=now()
        )
        self._pending_operations.append(
            dict(
                key="ops-batch:" + batch_id,
                method="message",
                text=f"Events: {len(selected)}\n\n" + "\n".join(cards),
                **common,
            )
        )
        if verbosity == "full" and attach_prompts:
            attachments = []
            for event in selected:
                attachment = event.copy()
                if not show_thoughts:
                    attachment.pop("thought", None)
                attachments.append(attachment)
            self._pending_operations.append(
                dict(
                    key="ops-batch-file:" + batch_id,
                    method="document",
                    filename=batch_id + ".json",
                    content=json.dumps(
                        {"events": attachments}, ensure_ascii=False, default=str
                    ),
                    **common,
                )
            )

    async def drain(self, *, force=False):
        # Persistence diagnostics must not enqueue more mirror work, even on failure.
        with structlog.contextvars.bound_contextvars(ops_mirror=True):
            async with self._draining:
                options = None
                while True:
                    if self._pending_operations is None:
                        while len(self._buffer) < 10:
                            try:
                                self._buffer.append(self.events.get_nowait())
                            except queue.Empty:
                                break
                        if not self._buffer or (
                            not force
                            and len(self._buffer) < 10
                            and self.clock() - self._buffer[0][0] < 5
                        ):
                            return
                        if options is None:
                            options = await asyncio.to_thread(self._options)
                        self._prepare(options)
                    # Keep the exact payload and keys until commit is acknowledged.
                    if self._pending_operations:
                        await asyncio.to_thread(
                            self.publisher.enqueue_operations, self._pending_operations
                        )
                    for _ in range(self._pending_count):
                        self._buffer.popleft()
                    self._pending_operations = None
                    self._pending_count = 0
