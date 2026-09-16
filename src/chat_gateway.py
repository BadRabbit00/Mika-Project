"""Background Telegram session controls and durable channel-specific replies."""

import asyncio
import json

import structlog

from src.core.time_utils import now
from src.publish import Destination


class ChatGateway:
    def __init__(self, service, publisher, layout, context_provider):
        self.service, self.publisher, self.layout = service, publisher, layout
        self.context_provider = context_provider

    async def handle(self, message, *, channel, trace_id):
        with structlog.contextvars.bound_contextvars(
            trace_id=trace_id, chat_channel=channel
        ):
            destination = (
                Destination("chat-dm", "mika", self.layout.owner_id)
                if channel == "dm"
                else self.layout.destination("chat")
            )
            text, document, reply_trace = None, False, None
            current = await asyncio.to_thread(self.service.store.active, channel)
            try:
                command, _, argument = message.text.partition(" ")
                if command.split("@", 1)[0] == "/chat":
                    argument = argument.strip() or "status"
                    if argument in {"on", "reset"}:
                        if argument == "reset" and current:
                            await self.service.close(current["id"], at=now())
                        current = await self.service.open(channel, at=now())
                        text = {"session": current["id"], "status": "open"}
                    elif argument == "off":
                        if current:
                            await self.service.close(current["id"], at=now())
                        text = {"status": "closed"}
                    elif argument == "status":
                        text = current or {"status": "closed"}
                    elif argument == "export":
                        if current is None:
                            with self.service.database.connection() as c:
                                row = c.execute(
                                    "SELECT id FROM sessions WHERE channel=? "
                                    "ORDER BY opened_at DESC LIMIT 1",
                                    (channel,),
                                ).fetchone()
                            if row:
                                current = {"id": row[0]}
                        if current:
                            text, document = (
                                await self.service.export(current["id"]),
                                True,
                            )
                        else:
                            text = {"status": "no_session"}
                    else:
                        raise ValueError("Use /chat on, off, status, reset, or export")
                elif command == "/facts":
                    if argument:
                        raise ValueError(
                            "Use the owner confirmation controls to delete facts"
                        )
                    facts, _, _, _ = await asyncio.to_thread(self.service._memory, None)
                    text, document = json.dumps(facts, ensure_ascii=False), True
                elif command.startswith("/"):
                    raise ValueError("Use /chat to control this dialogue channel")
                elif current:
                    if self.context_provider is None:
                        raise ValueError("current world and mood are required")
                    blocks = await self.context_provider()
                    result = await self.service.reply(
                        channel, message.text, trace_id=trace_id, **blocks
                    )
                    if result:
                        text = result.text
                        reply_trace = result.trace_id
            except ValueError as error:
                structlog.get_logger("blogai.chat_gateway").warning(
                    "chat_output_withheld", error_type=type(error).__name__
                )
                return
            if text is None:
                return
            content = (
                text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
            )
            if document or len(content) > 4096:
                data = dict(
                    method="document", content=content, filename=f"{trace_id}.jsonl"
                )
            else:
                data = dict(method="message", text=content)
            if reply_trace is not None:
                data["chat_reply"] = reply_trace
            await asyncio.to_thread(
                self.publisher.enqueue_operation,
                trace_id + ":chat",
                destination,
                trace_id=trace_id,
                **data,
            )
