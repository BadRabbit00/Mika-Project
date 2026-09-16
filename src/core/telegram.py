"""Explicit deployment layout and asynchronous aiogram outbox transport."""

from dataclasses import dataclass
from pathlib import Path

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from ruamel.yaml import YAML

from src.publish import DeliveryRejected, Destination


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

    @model_validator(mode="after")
    def validate_topics(self):
        if (
            set(self.topics) != TOPIC_ROLES.keys()
            or len(set(self.topics.values())) != 7
            or any(value <= 0 for value in self.topics.values())
        ):
            raise ValueError("Seven distinct positive topic IDs are required")
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


class TelegramTransport:
    def __init__(self, bots):
        self.bots = bots

    async def send(self, payload):
        destination = Destination(**payload["destination"])
        bot = self.bots[destination.bot]
        common = {"chat_id": destination.chat_id}
        if destination.topic_id is not None:
            common["message_thread_id"] = destination.topic_id
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
                case _:
                    raise ValueError("Unknown delivery method")
        except TelegramRetryAfter as error:
            raise DeliveryRejected(
                "Telegram flood control", retry_after=error.retry_after
            ) from error
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            raise DeliveryRejected(type(error).__name__) from error
        return result.message_id
