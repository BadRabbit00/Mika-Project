"""Factual day context and deterministic calendar-derived location."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from random import Random
from types import MappingProxyType

from ruamel.yaml import YAML

from src.core.pad import finite
from src.core.schedule import Blackout, Schedule, SleepWindow
from src.core.time_utils import daypart_at, elapsed_hours, require_aware
from src.core.weather import weather_relevant


@dataclass(frozen=True)
class DayContext:
    at: datetime
    bedtime: datetime
    wake_time: datetime
    sleep_debt: float
    location: str
    daypart: str
    available_objects: tuple[str, ...]
    blackout: Blackout

    def __post_init__(self):
        for key in ("at", "bedtime", "wake_time"):
            object.__setattr__(self, key, require_aware(getattr(self, key)))
        if finite(self.sleep_debt) < 0:
            raise ValueError("Sleep debt must be nonnegative")


class World:
    def __init__(self, life: dict, schedule: Schedule):
        self._life, self.schedule = life, schedule
        self.locations = MappingProxyType(
            {name: tuple(data["objects"]) for name, data in life["locations"].items()}
        )

    @classmethod
    def from_config(cls, directory: Path):
        life = YAML(typ="safe").load(
            (Path(directory) / "life.yaml").read_text(encoding="utf-8")
        )
        return cls(life, Schedule.from_config(directory))

    def day_context(
        self,
        at: datetime,
        *,
        sleep: SleepWindow,
        sleep_debt: float,
        location: str,
        road_roll: float,
    ) -> DayContext:
        at = require_aware(at)
        if location not in self.locations:
            raise ValueError("Unknown configured location")
        return DayContext(
            at,
            sleep.bedtime,
            sleep.wake,
            sleep_debt,
            location,
            daypart_at(at, self._life["dayparts"]),
            self.locations[location],
            self.schedule.blackout(at, sleep=sleep, road_roll=road_roll),
        )

    def where(self, at: datetime, *, sleep: SleepWindow) -> str:
        """Section 26.1, with a reproducible draw for the local calendar date."""
        at = require_aware(at)
        rng = Random(at.date().isoformat())
        if sleep.contains(at):
            return "дом"
        if self.schedule.has_classes(at) and 9 <= at.hour < 14:
            return rng.choices(["универ", "транспорт"], weights=[0.85, 0.15])[0]
        if 14 <= at.hour < 19:
            return rng.choices(["дом", "кофейня", "улица"], weights=[0.6, 0.25, 0.15])[
                0
            ]
        return "дом"

    async def relevant_weather(self, at, *, client, location, last_mention_at, rng):
        at = require_aware(at)
        if location not in self.locations:
            raise ValueError("Unknown configured location")
        days = (
            float("inf")
            if last_mention_at is None
            else elapsed_hours(last_mention_at, at) / 24
        )
        if days < 0:
            raise ValueError("A weather mention cannot be in the future")
        weather = await client.fetch(at, rng=rng)
        relevant = weather_relevant(
            weather,
            outdoors=location in ("улица", "транспорт"),
            last_mention_days=days,
            rng=rng,
            ordinary_chance=client.settings.get("world.weather_chance"),
            cooldown_days=self._life["greetings"]["weather_cooldown_days"],
        )
        return weather if relevant else None
