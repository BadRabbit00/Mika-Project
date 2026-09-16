"""Factual day context assembled from the calendar and caller's known location."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from ruamel.yaml import YAML

from src.core.pad import finite
from src.core.schedule import Blackout, Schedule, SleepWindow
from src.core.time_utils import daypart_at, require_aware


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
