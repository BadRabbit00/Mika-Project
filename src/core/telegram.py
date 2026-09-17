"""Explicit deployment layout and asynchronous aiogram outbox transport."""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

import structlog
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile, InputMediaDocument, InputMediaPhoto
from dotenv import dotenv_values
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from ruamel.yaml import YAML

from src.core.pad_plot import render_png
from src.publish import DeliveryRejected, Destination

log = structlog.get_logger("blogai.telegram")


@dataclass(frozen=True)
class Role:
    bot: str | None
    reads: str | None


TOPIC_ROLES = {
    "diary": Role("mika", None),
    "author": Role(None, None),
    "curator": Role("curator", None),
    "chat": Role("mika", "chat"),
    "library": Role("ops", "ingest"),
    "machine": Role("ops", None),
    "control": Role("ops", "commands"),
    "state": Role("ops", None),
}


class TelegramLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    owner_id: int = Field(gt=0)
    group_id: int = Field(
        lt=0, validation_alias=AliasChoices("supergroup_id", "group_id")
    )
    channel_id: int | None = None
    topics: dict[str, int]
    bots: dict[str, str] = Field(
        default_factory=lambda: {
            role: f"{role.upper()}_BOT_TOKEN" for role in ("mika", "curator", "ops")
        }
    )

    @field_validator("channel_id", mode="before")
    @classmethod
    def empty_channel_is_disabled(cls, value):
        if type(value) is int and value == 0:
            return None
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_topics(self):
        if (
            not (TOPIC_ROLES.keys() - {"state"}) <= self.topics.keys()
            or not self.topics.keys() <= TOPIC_ROLES.keys()
            or len(set(self.topics.values())) != len(self.topics)
            or any(value <= 0 for value in self.topics.values())
        ):
            raise ValueError(
                "Seven required topics and an optional state topic need "
                "distinct positive IDs"
            )
        if self.channel_id is not None and self.channel_id >= 0:
            raise ValueError("A public channel must have a negative chat ID")
        if (
            set(self.bots) != {"mika", "curator", "ops"}
            or len(set(self.bots.values())) != 3
        ):
            raise ValueError("Three distinct bot token environment names are required")
        if any(
            not value.isidentifier() or not value.isascii()
            for value in self.bots.values()
        ):
            raise ValueError(
                "Bot values must be environment variable names, never tokens"
            )
        return self

    @classmethod
    def from_file(cls, path: Path):
        return cls.model_validate(
            YAML(typ="safe").load(Path(path).read_text(encoding="utf-8"))
        )

    def topic(self, topic_id):
        return next(
            (key for key, value in self.topics.items() if value == topic_id), None
        )

    def destination(self, topic):
        role = TOPIC_ROLES[topic]
        if role.bot is None:
            raise ValueError("The author topic is written manually")
        return Destination(
            topic, role.bot, self.group_id, self.topics[topic], primary=topic == "diary"
        )

    def publication_destinations(self):
        destinations = [self.destination("diary")]
        if self.channel_id is not None:
            destinations.append(Destination("channel", "mika", self.channel_id))
        return destinations


def load_bot_tokens(
    layout: TelegramLayout, env_file: Path | None = None
) -> dict[str, str]:
    """Read credentials without mutating the process environment.

    Exported variables take precedence. Only an absent default .env is optional;
    an explicitly selected file must exist and be readable.
    """
    values = {}
    path = Path(env_file) if env_file is not None else Path(".env")
    try:
        with path.open(encoding="utf-8-sig") as stream:
            values = dotenv_values(stream=stream, interpolate=False)
    except FileNotFoundError:
        if env_file is not None:
            raise
    except UnicodeError:
        raise ValueError("Bot env file must contain UTF-8 text") from None
    tokens = {
        role: os.environ.get(name, values.get(name))
        for role, name in layout.bots.items()
    }
    missing = [
        layout.bots[role]
        for role, value in tokens.items()
        if value is None or not value.strip()
    ]
    if missing:
        raise ValueError(
            "Missing or empty Telegram bot token variables: " + ", ".join(missing)
        )
    if len(set(tokens.values())) != 3:
        raise ValueError("Three distinct Telegram bot tokens are required")
    return tokens


class TelegramTransport:
    def __init__(self, bots):
        self.bots = bots

    async def send(self, payload):
        destination = Destination(**payload["destination"])
        bot = self.bots[destination.bot]
        common = {"chat_id": destination.chat_id}
        if destination.topic_id is not None:
            common["message_thread_id"] = destination.topic_id
        if payload["method"] in {"photo", "edit_photo"}:
            try:
                content = await asyncio.to_thread(render_png, payload["pad_plot"])
                photo = BufferedInputFile(content, filename="mika-state.png")
            except Exception as error:
                # Nothing has reached Telegram; this failure is safe to retry.
                log.exception("pad_render_failed", trace_id=payload.get("trace_id"))
                raise DeliveryRejected("PAD image rendering failed") from error
        try:
            match payload["method"]:
                case "message":
                    result = await bot.send_message(
                        **common,
                        text=payload["text"],
                        parse_mode=payload.get("parse_mode"),
                        reply_markup=payload.get("reply_markup"),
                    )
                case "document":
                    document = BufferedInputFile(
                        payload["content"].encode("utf-8"), filename=payload["filename"]
                    )
                    result = await bot.send_document(
                        **common,
                        document=document,
                        caption=payload.get("caption"),
                        reply_markup=payload.get("reply_markup"),
                    )
                case "photo":
                    result = await bot.send_photo(
                        **common,
                        photo=photo,
                        caption=payload.get("caption"),
                        show_caption_above_media=True,
                    )
                case "edit_photo":
                    result = await bot.edit_message_media(
                        chat_id=destination.chat_id,
                        message_id=payload["message_id"],
                        media=InputMediaPhoto(
                            media=photo,
                            caption=payload.get("caption"),
                            show_caption_above_media=True,
                        ),
                    )
                case "edit":
                    result = await bot.edit_message_text(
                        chat_id=destination.chat_id,
                        message_id=payload["message_id"],
                        text=payload["text"],
                        parse_mode=payload.get("parse_mode"),
                        reply_markup=payload.get("reply_markup"),
                    )
                case "pin":
                    await bot.pin_chat_message(
                        chat_id=destination.chat_id,
                        message_id=payload["message_id"],
                        disable_notification=True,
                    )
                    return payload["message_id"]
                case "edit_document":
                    result = await bot.edit_message_media(
                        chat_id=destination.chat_id,
                        message_id=payload["message_id"],
                        media=InputMediaDocument(
                            media=BufferedInputFile(
                                payload["content"].encode("utf-8"),
                                filename=payload["filename"],
                            ),
                            caption=payload.get("caption"),
                        ),
                    )
                case _:
                    raise ValueError("Unknown delivery method")
        except TelegramRetryAfter as error:
            raise DeliveryRejected(
                "Telegram flood control", retry_after=error.retry_after
            ) from error
        except TelegramBadRequest as error:
            if (
                payload["method"] == "edit_photo"
                and "message is not modified" in error.message.lower()
            ):
                # Telegram confirms that this message already has the desired media.
                return payload["message_id"]
            raise DeliveryRejected(type(error).__name__) from error
        except TelegramForbiddenError as error:
            raise DeliveryRejected(type(error).__name__) from error
        return result.message_id
