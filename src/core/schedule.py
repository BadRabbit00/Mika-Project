"""Explicit sleep plans, actual wake events, and publication blackouts."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from random import Random

import structlog
from ruamel.yaml import YAML

from src.core.pad import clamp, finite
from src.core.time_utils import (
    add_elapsed,
    elapsed_hours,
    in_clock_window,
    local_clock,
    require_aware,
    to_utc_iso,
)

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
log = structlog.get_logger("blogai.schedule")


class ScheduleGap(ValueError):
    """A documented scheduling decision needs explicit caller input."""


@dataclass(frozen=True)
class SleepWindow:
    bedtime: datetime
    wake: datetime

    def __post_init__(self):
        object.__setattr__(self, "bedtime", require_aware(self.bedtime))
        object.__setattr__(self, "wake", require_aware(self.wake))
        if self.hours <= 0:
            raise ValueError("Wake must follow bedtime")

    @property
    def hours(self) -> float:
        return elapsed_hours(self.bedtime, self.wake)

    def contains(self, at: datetime) -> bool:
        return (
            self.bedtime.timestamp()
            <= require_aware(at).timestamp()
            < self.wake.timestamp()
        )


@dataclass(frozen=True)
class ClassSlot:
    start: datetime
    end: datetime
    subject: str
    event_id: str

    def __post_init__(self):
        object.__setattr__(self, "start", require_aware(self.start))
        object.__setattr__(self, "end", require_aware(self.end))
        if elapsed_hours(self.start, self.end) <= 0:
            raise ValueError("Class end must follow its start")

    def contains(self, at: datetime) -> bool:
        return (
            self.start.timestamp()
            <= require_aware(at).timestamp()
            < self.end.timestamp()
        )


@dataclass(frozen=True)
class WakeEvent:
    reason: str
    at: datetime
    event_id: str | None
    hint: str | None = None
    missed_first_class: bool = False

    def __post_init__(self):
        object.__setattr__(self, "at", require_aware(self.at))


@dataclass(frozen=True)
class Blackout:
    blocked: bool
    reason: str | None = None
    label: str | None = None


class Schedule:
    def __init__(self, data: dict, sleep: dict, *, semester=None):
        if data["timezone"] != "Asia/Almaty":
            raise ValueError("The schedule must use Asia/Almaty")
        self._data, self._sleep = data, sleep
        self._semester = semester

    @classmethod
    def from_config(cls, directory: Path):
        yaml = YAML(typ="safe")
        data = yaml.load(
            (Path(directory) / "schedule.yaml").read_text(encoding="utf-8")
        )
        life = yaml.load((Path(directory) / "life.yaml").read_text(encoding="utf-8"))
        return cls(data, life["sleep"], semester=life.get("semester"))

    def semester_week(self, at: datetime) -> int | None:
        at = require_aware(at)
        if self._semester is None:
            raise ScheduleGap("A semester start date is required")
        week = (at.date() - self._semester["start"]).days // 7 + 1
        return week if 1 <= week <= self._semester["weeks"] else None

    def mood_label(self, *, learning_state, rounds, p) -> str | None:
        rules = self._data["mood_labels"]
        if (
            learning_state == rules["stuck"]["learning_state"]
            and rounds >= rules["stuck"]["min_rounds"]
        ):
            return "stuck"
        return "down" if finite(p) < rules["down"]["p_below"] else None

    def classes(self, day: datetime) -> tuple[ClassSlot, ...]:
        day = require_aware(day)
        weekday = WEEKDAYS[day.weekday()]
        return tuple(
            ClassSlot(
                local_clock(day, item["from"]),
                local_clock(day, item["to"]),
                item["subject"],
                f"class:{weekday}:{index}",
            )
            for index, item in enumerate(self._data["university"][weekday])
        )

    def has_classes(self, day: datetime) -> bool:
        return bool(self.classes(day))

    def event_catalog(self) -> dict[str, dict]:
        """Expose configured deltas for registration by MoodModel."""
        events = {
            f"wake:{item['id']}": {"delta": item["mood"]}
            for item in self._data["wake"]["interruptions"]
        }
        for weekday, items in self._data["university"].items():
            for index, item in enumerate(items):
                events[f"class:{weekday}:{index}"] = {"delta": item["mood"]}
        return events

    def plan_bedtime(
        self, day: datetime, *, last_complexity: int, mood: str | None, rng: Random
    ) -> datetime:
        day = require_aware(day)
        if type(last_complexity) is not int or not 1 <= last_complexity <= 10:
            raise ValueError("Article complexity must be an integer from one to ten")
        if mood not in (None, "neutral", "stuck", "down"):
            raise ValueError("Supply an explicit neutral, stuck, or down sleep label")
        next_day = day + timedelta(days=1)
        base = local_clock(next_day, self._sleep["bedtime_base"])
        delta = self._sleep["per_complexity_point"] * max(0, last_complexity - 5)
        delta += self._sleep["stuck_bonus_min"] if mood == "stuck" else 0
        delta += self._sleep["down_penalty_min"] if mood == "down" else 0
        bedtime = add_elapsed(
            base, minutes=delta + rng.gauss(0, self._sleep["jitter_min"])
        )
        log.info(
            "bedtime_planned",
            bedtime=to_utc_iso(bedtime),
            complexity=last_complexity,
            sleep_label=mood,
        )
        return bedtime

    def resolve_wake(
        self,
        day: datetime,
        *,
        rng: Random,
        interruption_times: Mapping[str, datetime] | None = None,
        trigger_states: Mapping[str, bool],
    ) -> WakeEvent:
        day = require_aware(day)
        weekday = WEEKDAYS[day.weekday()]
        classes = self.classes(day)
        for item in self._data["wake"]["interruptions"]:
            when = item.get("when", {})
            if weekday not in when.get("weekday", WEEKDAYS):
                continue
            if "has_classes" in when and when["has_classes"] != bool(classes):
                continue
            if required := item.get("requires_trigger_state"):
                if required not in trigger_states:
                    raise ScheduleGap(f"Missing trigger state: {required}")
                if type(trigger_states[required]) is not bool:
                    raise ValueError("Trigger states must be boolean")
                if not trigger_states[required]:
                    continue
            if rng.random() >= item["chance"]:
                continue
            if interruption_times and item["id"] in interruption_times:
                at = require_aware(interruption_times[item["id"]])
            elif window := item.get("window", when.get("window")):
                if window[0].startswith("+"):
                    if not classes:
                        raise ScheduleGap("An alarm-relative wake needs a class day")
                    alarm = add_elapsed(
                        classes[0].start,
                        minutes=-self._data["alarm_before_first_class_min"],
                    )
                    low, high = [
                        int(h) * 60 + int(m)
                        for h, m in (part[1:].split(":") for part in window)
                    ]
                    at = add_elapsed(alarm, minutes=rng.uniform(low, high))
                else:
                    at = self._uniform_time(day, window, rng)
            else:
                raise ScheduleGap(f"Missing wake window for {item['id']}")
            if at.date() != day.date():
                raise ValueError("Interruption time must belong to the wake day")
            return self._wake_event(
                WakeEvent(
                    item["id"],
                    at,
                    f"wake:{item['id']}",
                    item["text_hint"],
                    item.get("effect", {}).get("missed_first_class", False),
                )
            )
        if classes:
            at = add_elapsed(
                classes[0].start, minutes=-self._data["alarm_before_first_class_min"]
            )
            return self._wake_event(WakeEvent("alarm", at, "woke_by_alarm_early"))
        key = "weekend" if day.weekday() >= 5 else "weekday_no_classes"
        at = self._uniform_time(day, self._data["wake"]["free_window"][key], rng)
        return self._wake_event(WakeEvent("free", at, None))

    @staticmethod
    def _uniform_time(day: datetime, window: list[str], rng: Random) -> datetime:
        lo, hi = (local_clock(day, clock) for clock in window)
        if elapsed_hours(lo, hi) < 0:
            raise ValueError("Wake windows must lie within one local day")
        return add_elapsed(lo, hours=rng.uniform(0, elapsed_hours(lo, hi)))

    @staticmethod
    def _wake_event(event: WakeEvent) -> WakeEvent:
        log.info(
            "wake_selected",
            reason=event.reason,
            at=to_utc_iso(event.at),
            missed_first_class=event.missed_first_class,
        )
        return event

    def sleep_debt(self, debt: float, sleep: SleepWindow) -> float:
        if finite(debt) < 0:
            raise ValueError("Sleep debt must be nonnegative")
        sleep_need = self._data["wake"]["sleep_need_hours"]
        debt_cap = self._data["wake"]["debt_cap_hours"]
        actual_hours = sleep.hours
        return clamp(debt + (sleep_need - actual_hours), 0, debt_cap)

    def in_commute(self, at: datetime) -> bool:
        at = require_aware(at)
        return any(self._commute_contains(item, at) for item in self._data["blackout"])

    def _commute_contains(self, item, at):
        if derive := item.get("derive"):
            classes = self.classes(at)
            if not classes:
                return False
            start = add_elapsed(classes[0].start, minutes=-derive["minus_minutes"])
            end = add_elapsed(start, minutes=derive["length_minutes"])
            return start.timestamp() <= at.timestamp() < end.timestamp()
        return any(in_clock_window(at, *window) for window in item.get("windows", []))

    def blackout(
        self, at: datetime, *, sleep: SleepWindow, road_roll: float
    ) -> Blackout:
        at = require_aware(at)
        if not 0 <= finite(road_roll) < 1:
            raise ValueError("Road probability roll must lie in [0, 1)")
        for item in self._data["blackout"]:
            if item.get("source") == "sleep_model" and sleep.contains(at):
                return Blackout(True, "sleep", item["reason"])
            if item.get("source") == "university":
                if any(
                    add_elapsed(
                        lesson.start, minutes=-item["grace_before_min"]
                    ).timestamp()
                    <= at.timestamp()
                    < add_elapsed(
                        lesson.end, minutes=item["grace_after_min"]
                    ).timestamp()
                    for lesson in self.classes(at)
                ):
                    return Blackout(True, "class", item["reason"])
            if self._commute_contains(item, at):
                if road_roll >= item["chance_to_post"]:
                    return Blackout(True, "commute", item["reason"])
        return Blackout(False)
