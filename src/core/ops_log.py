"""Mirror event cards to Telegram while JSONL remains the full local record."""

import asyncio
import json
import logging
import queue
from uuid import uuid4

import structlog


class OpsMirror(logging.Handler):
    def __init__(self, publisher, destination, registry):
        super().__init__()
        self.publisher, self.destination, self.registry = (
            publisher,
            destination,
            registry,
        )
        self.events = queue.SimpleQueue()

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
        self.events.put(event)

    async def drain(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return
            verbosity = await asyncio.to_thread(self.registry.get, "log.verbosity")
            if (
                verbosity == "quiet"
                and event["level"] not in {"error", "critical"}
                and not event["event"].startswith("state_transition")
            ):
                continue
            trace_id = event["trace_id"]
            card = "\n".join(
                str(value)
                for value in (
                    event["event"],
                    f"trace_id: {trace_id}",
                    f"actor: {event.get('actor', event.get('logger', 'system'))}",
                    f"status: {event.get('status', event['level'])}",
                    f"cost_usd: {event.get('cost_usd', 'unknown')}",
                )
            )
            with structlog.contextvars.bound_contextvars(
                ops_mirror=True, trace_id=trace_id
            ):
                await asyncio.to_thread(
                    self.publisher.enqueue_operation,
                    "event:" + event["event_id"],
                    self.destination,
                    trace_id=trace_id,
                    method="message",
                    text=card[:4096],
                )
                if verbosity == "full" and await asyncio.to_thread(
                    self.registry.get, "log.attach_prompts"
                ):
                    attachment = event.copy()
                    if not await asyncio.to_thread(
                        self.registry.get, "log.show_thoughts"
                    ):
                        attachment.pop("thought", None)
                    await asyncio.to_thread(
                        self.publisher.enqueue_operation,
                        "event-file:" + event["event_id"],
                        self.destination,
                        trace_id=trace_id,
                        method="document",
                        filename=event["event_id"] + ".json",
                        content=json.dumps(attachment, ensure_ascii=False, default=str),
                    )
