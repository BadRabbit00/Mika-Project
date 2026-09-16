"""Immutable, validated scalar PAD values and inertia coefficients."""

import math
from dataclasses import dataclass

AXES = ("P", "A", "D")


def finite(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError("Expected a finite number")
    return value


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def sign(value: float) -> int:
    return (value > 0) - (value < 0)


@dataclass(frozen=True)
class Mood:
    P: float
    A: float
    D: float

    def __post_init__(self):
        for axis in AXES:
            if not -1 <= finite(getattr(self, axis)) <= 1:
                raise ValueError("PAD values must be between -1 and 1")


@dataclass(frozen=True)
class Coefficients:
    out_pos: float
    out_neg: float
    inward_boost: float
    pierce_floor: float

    def __post_init__(self):
        if min(finite(self.out_pos), finite(self.out_neg)) <= 0:
            raise ValueError("Outward exponents must be positive")
        if finite(self.inward_boost) < 0 or not 0 <= finite(self.pierce_floor) <= 1:
            raise ValueError("Invalid inward boost or piercing floor")
