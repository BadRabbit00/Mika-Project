"""Calendar-based coefficient modifiers; never rendered into model context."""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from random import Random
from types import MappingProxyType

from src.core.pad import AXES, Mood
from src.core.time_utils import require_aware


@dataclass(frozen=True)
class CycleEffect:
    day: int
    id: str
    baseline: Mood
    coef_mult: Mapping[str, Mapping[str, float]]
    cycle_number: int = 0
    length: int = 28


class Cycle:
    def __init__(self, config: dict, *, epoch: datetime, enabled: bool = True):
        self.epoch = require_aware(epoch)
        self._config = config
        self.enabled = enabled and config["enabled"]
        fixed = config["phase_lengths"]
        minimum = config["length_days"] + min(config["length_jitter_days"])
        if minimum <= sum(fixed.values()) or not 1 <= config["start_day"] <= minimum:
            raise ValueError("Cycle length and initial ordinal day are inconsistent")
        if set(fixed) != {"menstrual", "ovulatory", "luteal"}:
            raise ValueError("Only the follicular phase can have variable length")

    def length(self, number: int) -> int:
        identity = f"{self.epoch.date().isoformat()}:{number}".encode()
        seed = int.from_bytes(hashlib.sha256(identity).digest())
        low, high = self._config["length_jitter_days"]
        return self._config["length_days"] + Random(seed).randint(low, high)

    def at(self, at: datetime, *, enabled: bool = True) -> CycleEffect:
        at = require_aware(at)
        if not self.enabled or not enabled:
            return CycleEffect(0, "disabled", Mood(0, 0, 0), MappingProxyType({}))
        elapsed_days = (at.date() - self.epoch.date()).days
        offset, number = elapsed_days + self._config["start_day"] - 1, 0
        while offset < 0:
            number -= 1
            offset += self.length(number)
        while offset >= self.length(number):
            offset -= self.length(number)
            number += 1
        length, day = self.length(number), offset + 1
        fixed = self._config["phase_lengths"]
        lengths = (
            fixed["menstrual"],
            length - sum(fixed.values()),
            fixed["ovulatory"],
            fixed["luteal"],
        )
        end = 0
        phase_id = None
        for name, duration in zip(
            ("menstrual", "follicular", "ovulatory", "luteal"), lengths, strict=True
        ):
            end += duration
            if day <= end:
                phase_id = name
                break
        phase = next(item for item in self._config["phases"] if item["id"] == phase_id)
        baseline = dict(phase["baseline"])
        if phase_id == "luteal" and day > length - self._config["luteal_late_last_n"]:
            for axis, extra in phase["late_extra"].items():
                baseline[axis] = baseline[axis] + extra
        coefficients = MappingProxyType(
            {
                axis: MappingProxyType(dict(values))
                for axis, values in phase["coef_mult"].items()
            }
        )
        return CycleEffect(
            day,
            phase["id"],
            Mood(*(baseline[axis] for axis in AXES)),
            coefficients,
            number,
            length,
        )
