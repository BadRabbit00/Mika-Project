"""Calendar-based coefficient modifiers; never rendered into model context."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from src.core.pad import AXES, Mood
from src.core.time_utils import require_aware


@dataclass(frozen=True)
class CycleEffect:
    day: int
    id: str
    baseline: Mood
    coef_mult: Mapping[str, Mapping[str, float]]


class Cycle:
    def __init__(self, config: dict, *, epoch: datetime, enabled: bool = True):
        self.epoch = require_aware(epoch)
        self._config = config
        self.enabled = enabled and config["enabled"]
        days = [
            day
            for phase in config["phases"]
            for day in range(phase["days"][0], phase["days"][1] + 1)
        ]
        if sorted(days) != list(range(1, config["length_days"] + 1)):
            raise ValueError("Cycle phases must partition all cycle days")

    def at(self, at: datetime) -> CycleEffect:
        at = require_aware(at)
        if not self.enabled:
            return CycleEffect(0, "disabled", Mood(0, 0, 0), MappingProxyType({}))
        elapsed_days = (at.date() - self.epoch.date()).days
        day = (elapsed_days + self._config["start_offset_days"]) % self._config[
            "length_days"
        ] + 1
        phase = next(
            item
            for item in self._config["phases"]
            if item["days"][0] <= day <= item["days"][1]
        )
        baseline = dict(phase["baseline"])
        if (
            "late_days" in phase
            and phase["late_days"][0] <= day <= phase["late_days"][1]
        ):
            for axis, extra in phase["late_extra"].items():
                baseline[axis] = baseline[axis] + extra
        coefficients = MappingProxyType(
            {
                axis: MappingProxyType(dict(values))
                for axis, values in phase["coef_mult"].items()
            }
        )
        return CycleEffect(
            day, phase["id"], Mood(*(baseline[axis] for axis in AXES)), coefficients
        )
