"""Three bot identities, strict topic ingress, and nonblocking command dispatch."""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import timedelta

import structlog
from aiogram import Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.telegram import TOPIC_ROLES
from src.core.time_utils import now

log = structlog.get_logger("blogai.bot")


def post_buttons(post_id, *, published=False):
    actions = (
        [("Брак", "defect")]
        if published
        else [("Опубликовать", "publish"), ("Перегенерировать", "regen")]
    )
    buttons = []
    for title, action in actions:
        data = f"post:{action}:{post_id}"
        if len(data.encode()) > 64:
            raise ValueError("Post identity exceeds Telegram callback capacity")
        buttons.append(InlineKeyboardButton(text=title, callback_data=data))
    return InlineKeyboardMarkup(inline_keyboard=[buttons]).model_dump(exclude_none=True)


def settings_buttons(items):
    rows = []
    for selector, title in items:
        data = f"settings:view:{selector}"
        if len(data.encode()) > 64:
            raise ValueError("Setting identity exceeds Telegram callback capacity")
        rows.append([InlineKeyboardButton(text=title, callback_data=data)])
    return InlineKeyboardMarkup(inline_keyboard=rows).model_dump(exclude_none=True)


@dataclass(frozen=True)
class PendingDefect:
    post_id: str
    trace_id: str
    expires_at: object


class BotIngress:
    def __init__(self, layout, bot_roles, jobs, service):
        self.layout, self.bot_roles, self.jobs, self.service = (
            layout,
            bot_roles,
            jobs,
            service,
        )
        self.pending_defects = {}
        self.router = Router(name="blogai-ingress")
        self.router.message.register(self.on_message)
        self.router.callback_query.register(self.on_callback)

    def _origin(self, message, bot):
        if message.chat.type == "private":
            if (
                message.chat.id == self.layout.owner_id
                and self.bot_roles.get(bot.id) == "mika"
            ):
                return "chat_private"
            return (
                "private"
                if message.chat.id == self.layout.owner_id
                and self.bot_roles.get(bot.id) == "ops"
                else None
            )
        if message.chat.id != self.layout.group_id:
            return None
        topic = self.layout.topic(message.message_thread_id)
        if topic is None or TOPIC_ROLES[topic].bot != self.bot_roles.get(bot.id):
            return None
        return topic

    @staticmethod
    def _trace(message, bot):
        return f"tg-{bot.id}-{message.chat.id}-{message.message_id}"

    def _submit(self, trace_id, kind, work):
        try:
            self.jobs.submit(trace_id, kind, work)
        except asyncio.QueueFull:
            log.error("job_queue_full", trace_id=trace_id, kind=kind)

    async def on_message(self, message: Message, bot):
        user = message.from_user
        if user is None or user.is_bot or user.id != self.layout.owner_id:
            return
        origin = self._origin(message, bot)
        if origin not in {
            "library",
            "machine",
            "control",
            "private",
            "chat",
            "chat_private",
        }:
            return
        trace_id = self._trace(message, bot)
        if (
            origin in {"chat", "chat_private"}
            and message.text
            and message.text.split(" ", 1)[0] in {"/facts", "/forget-fact"}
        ):
            self._submit(
                trace_id,
                "command",
                lambda: self.service.command(message, bot, trace_id),
            )
            return
        if origin in {"chat", "chat_private"}:
            if message.text:
                self._submit(
                    trace_id,
                    "chat",
                    lambda: self.service.chat(
                        message,
                        bot,
                        trace_id,
                        channel="dm" if origin == "chat_private" else "topic",
                    ),
                )
            return
        if origin == "library":
            if message.document is not None:
                self._submit(
                    trace_id,
                    "library",
                    lambda: self.service.document(message, bot, trace_id),
                )
            return
        pending = self.pending_defects.get(user.id)
        if (
            pending
            and origin in {"control", "private"}
            and message.text
            and not message.text.startswith("/")
        ):
            del self.pending_defects[user.id]
            if pending.expires_at >= now():
                self._submit(
                    pending.trace_id,
                    "defect",
                    lambda: self.service.defect(
                        pending.post_id, message.text, message, pending.trace_id
                    ),
                )
            return
        if message.text and message.text.startswith("/"):
            self._submit(
                trace_id,
                "command",
                lambda: self.service.command(message, bot, trace_id),
            )

    async def on_callback(self, query, bot):
        if query.from_user.id != self.layout.owner_id or not isinstance(
            query.message, Message
        ):
            return
        origin = self._origin(query.message, bot)
        if origin not in {"diary", "machine", "control", "private"} or not query.data:
            return
        parts = query.data.split(":", 2)
        trace_id = "callback-" + hashlib.sha256(query.id.encode()).hexdigest()[:24]
        if len(parts) == 3 and parts[:2] == ["facts", "confirm"]:
            if origin not in {"machine", "control", "private"}:
                return
            await query.answer()
            self._submit(
                trace_id,
                "fact-confirmation",
                lambda: self.service.confirm(
                    parts[2], query.message, trace_id, owner_id=query.from_user.id
                ),
            )
            return
        if len(parts) == 3 and parts[:2] == ["settings", "view"]:
            if origin not in {"machine", "control", "private"}:
                return
            await query.answer()
            self._submit(
                trace_id,
                "settings",
                lambda: self.service.settings_view(query.message, parts[2], trace_id),
            )
            return
        if (
            len(parts) != 3
            or parts[0] != "post"
            or parts[1] not in {"publish", "regen", "defect"}
        ):
            return
        _, action, post_id = parts
        if origin == "diary" and action != "defect":
            return
        await query.answer()
        if action == "defect":
            self.pending_defects[query.from_user.id] = PendingDefect(
                post_id, trace_id, now() + timedelta(minutes=1)
            )
        self._submit(
            trace_id,
            action,
            lambda: self.service.action(action, post_id, query.message, trace_id),
        )
