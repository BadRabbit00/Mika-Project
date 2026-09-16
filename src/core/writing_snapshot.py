"""Typed, prompt-free inputs for draft inspection and regeneration."""

import json
from dataclasses import asdict, dataclass

from src.core.pad import Mood
from src.core.schedule import Blackout
from src.core.time_utils import from_utc_iso, to_utc_iso
from src.core.world import DayContext


@dataclass(frozen=True)
class WritingSnapshot:
    kind: str
    day: DayContext
    mood: Mood
    wake_reason: str
    payload: dict
    bands: dict
    node_ids: tuple[str, ...] = ()
    thread_ids: tuple[int, ...] = ()

    def encode(self):
        value = asdict(self)
        for name in ("at", "bedtime", "wake_time"):
            value["day"][name] = to_utc_iso(getattr(self.day, name))

        def rounded(item):
            if isinstance(item, dict):
                return {key: rounded(val) for key, val in item.items()}
            if isinstance(item, (tuple, list)):
                return [rounded(val) for val in item]
            return round(item, 4) if type(item) is float else item

        return json.dumps(rounded(value), ensure_ascii=False, allow_nan=False)

    @classmethod
    def decode(cls, text):
        value = json.loads(text)
        day = value.pop("day")
        for name in ("at", "bedtime", "wake_time"):
            day[name] = from_utc_iso(day[name])
        day["available_objects"] = tuple(day["available_objects"])
        day["blackout"] = Blackout(**day["blackout"])
        value["mood"] = Mood(**value["mood"])
        value["node_ids"] = tuple(value["node_ids"])
        value["thread_ids"] = tuple(value["thread_ids"])
        return cls(day=DayContext(**day), **value)
