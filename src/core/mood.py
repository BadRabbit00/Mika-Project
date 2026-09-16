"""Literal PAD dynamics with immutable views and transactional event history."""

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from random import Random

import structlog
from ruamel.yaml import YAML

from src.core.cycle import Cycle
from src.core.db import Database, insert_mood
from src.core.pad import AXES, Coefficients, Mood, clamp, finite, sign
from src.core.time_utils import (
    add_elapsed,
    elapsed_hours,
    from_utc_iso,
    require_aware,
    to_utc_iso,
)

log = structlog.get_logger("blogai.mood")


@lru_cache(maxsize=1)
def _defaults():
    return YAML(typ="safe").load(Path("config/mood.yaml").read_text(encoding="utf-8"))


def apply(
    axis: str,
    cur: float,
    delta: float,
    pierce: bool = False,
    *,
    coefficients: Coefficients | None = None,
) -> float:
    if axis not in AXES or not -1 <= finite(cur) <= 1:
        raise ValueError("Invalid axis or current PAD value")
    finite(delta)
    c = coefficients or Coefficients(**_defaults()["coefficients"][axis])
    outward = (cur == 0) or (sign(delta) == sign(cur))
    if outward:
        k = c.out_pos if delta > 0 else c.out_neg
        resist = (1 - abs(cur)) ** k
    else:
        resist = 1 + abs(cur) * c.inward_boost
    if pierce:
        resist = max(resist, c.pierce_floor)
    return clamp(cur + delta * resist, -1.0, 1.0)


def decay(
    axis: str,
    cur: float,
    *,
    baseline: float,
    hours: float,
    half_life: float | None = None,
) -> float:
    if (
        axis not in AXES
        or not -1 <= finite(cur) <= 1
        or not -1 <= finite(baseline) <= 1
    ):
        raise ValueError("Invalid decay axis or PAD value")
    if finite(hours) < 0:
        raise ValueError("Decay cannot run backwards")
    if half_life is None:
        half_life = _defaults()["decay"][axis]["half_life_hours"]
    if finite(half_life) <= 0:
        raise ValueError("A positive half-life is required")
    return cur + (baseline - cur) * (1 - 0.5 ** (hours / half_life))


@dataclass(frozen=True)
class BaselineContext:
    sleep_debt: float = 0
    stuck_days: int = 0
    exam_result: str | None = None
    correction_recent: bool = False
    quiz_streak_good: bool = False

    def __post_init__(self):
        if (
            finite(self.sleep_debt) < 0
            or type(self.stuck_days) is not int
            or self.stuck_days < 0
        ):
            raise ValueError("Sleep debt and stuck days must be nonnegative")
        if self.exam_result not in (None, "passed", "failed"):
            raise ValueError("Unknown exam result")
        if (
            type(self.correction_recent) is not bool
            or type(self.quiz_streak_good) is not bool
        ):
            raise ValueError("Baseline flags must be boolean")


@dataclass(frozen=True)
class MoodSnapshot:
    at: datetime
    mood: Mood
    baseline: Mood
    sleep_debt: float
    last_event: str | None = None
    trigger_id: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "at", require_aware(self.at))


@dataclass(frozen=True)
class Resolution:
    id: int
    fire_at: datetime
    event_id: str
    trigger_id: str


class PendingResolution(ValueError):
    """Consume a scheduled resolution before advancing mood time."""


class TriggerBlocked(ValueError):
    """A configured frequency or exam guard rejected a trigger."""


class UnspecifiedResolution(ValueError):
    """The selected probability mass has no configured outcome."""


def _minute(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError("Invalid local clock time")
    return hour * 60 + minute


class MoodModel:
    def __init__(self, data: dict, life: dict, settings: dict, *, epoch: datetime):
        self._data, self._life, self._settings = data, life, settings
        self.base_coefficients = {
            axis: Coefficients(**data["coefficients"][axis]) for axis in AXES
        }
        self.cycle = Cycle(
            data["cycle"], epoch=epoch, enabled=settings["mood.cycle_enabled"]
        )
        self.events = {**data["events"], **data["events_world"]}
        self.triggers = {item["id"]: item for item in data["triggers"]["items"]}
        for event in self.events.values():
            if len(event["delta"]) != 3:
                raise ValueError("Events must specify exactly three deltas")
            for value in event["delta"]:
                finite(value)
        self.trigger_rules = data["triggers"]["rules"]

    @classmethod
    def from_config(cls, directory: Path, *, epoch: datetime):
        yaml = YAML(typ="safe")

        def read(name):
            return yaml.load((Path(directory) / name).read_text(encoding="utf-8"))

        registry = read("settings.yaml")
        settings = {
            item["key"]: item["default"]
            for item in registry["settings"]
            if item["key"].startswith("mood.")
        }
        return cls(read("mood.yaml"), read("life.yaml"), settings, epoch=epoch)

    def daypart(self, at: datetime) -> str:
        at = require_aware(at)
        minute = at.hour * 60 + at.minute
        for name, window in self._life["dayparts"].items():
            start, end = _minute(window["from"]), _minute(window["to"])
            if (
                start <= minute < end
                if start < end
                else minute >= start or minute < end
            ):
                return name
        raise ValueError("No configured daypart covers this instant")

    def coefficients(self, at: datetime) -> dict[str, Coefficients]:
        effect = self.cycle.at(at)
        return {
            axis: replace(
                c,
                **{
                    key: getattr(c, key) * multiplier
                    for key, multiplier in effect.coef_mult.get(axis, {}).items()
                },
            )
            for axis, c in self.base_coefficients.items()
        }

    def baseline(self, at: datetime, context: BaselineContext) -> Mood:
        daypart = self.daypart(at)
        effect = self.cycle.at(at)
        factors = {
            "sleep_debt_per_hour": context.sleep_debt,
            "stuck_days": context.stuck_days,
            "exam_passed_recent": int(context.exam_result == "passed"),
            "exam_failed_recent": int(context.exam_result == "failed"),
            "correction_recent": int(context.correction_recent),
            "quiz_streak_good": int(context.quiz_streak_good),
        }
        values = []
        for axis in AXES:
            config = self._data["baseline"][axis]
            value = config["value"] + config.get("by_daypart", {}).get(daypart, 0)
            for modifier, coefficient in config["modifiers"].items():
                value += factors[modifier] * coefficient
            value += getattr(effect.baseline, axis)
            values.append(clamp(value, *self._data["clamp_baseline"]))
        return Mood(*values)

    def decay(
        self, state: MoodSnapshot, at: datetime, context: BaselineContext
    ) -> MoodSnapshot:
        at = require_aware(at)
        hours = elapsed_hours(state.at, at)
        baseline = self.baseline(at, context)
        mood = Mood(
            *(
                decay(
                    axis,
                    getattr(state.mood, axis),
                    baseline=getattr(baseline, axis),
                    hours=hours,
                    half_life=self._data["decay"][axis]["half_life_hours"]
                    * self._settings["mood.decay_mult"],
                )
                for axis in AXES
            )
        )
        return MoodSnapshot(
            at, mood, baseline, context.sleep_debt, state.last_event, state.trigger_id
        )

    def changed(self, state: MoodSnapshot, delta: list[float], *, pierce: bool) -> Mood:
        coefficients = self.coefficients(state.at)
        if not self._settings["mood.enabled"]:
            raise ValueError(
                "TODO(MOOD-DISABLED): disabled-state semantics are not specified"
            )
        return Mood(
            *(
                apply(
                    axis,
                    getattr(state.mood, axis),
                    value * self._settings["mood.volatility"],
                    pierce=pierce,
                    coefficients=coefficients[axis],
                )
                for axis, value in zip(AXES, delta, strict=True)
            )
        )

    def band_for(self, axis: str, value: float) -> dict:
        if axis not in AXES or not -1 <= finite(value) <= 1:
            raise ValueError("Invalid mood band value")
        for band in self._data["bands"][axis]:
            low, high = band["range"]
            if low <= value < high or value == high == 1:
                return band
        raise ValueError("No mood band covers this value")

    @staticmethod
    def octant(mood: Mood) -> str:
        return "".join("+" if getattr(mood, axis) >= -0.15 else "-" for axis in AXES)

    def mood_block(self, mood: Mood) -> str:
        octant = self._data["octants"][self.octant(mood)]
        return "\n".join(
            [
                octant["name"],
                octant["hint"],
                *(self.band_for(axis, getattr(mood, axis))["text"] for axis in AXES),
            ]
        )


class MoodService:
    def __init__(
        self,
        database: Database,
        model: MoodModel,
        *,
        initial: Mood,
        initial_at: datetime,
    ):
        self.database, self.model = database, model
        self._initial = MoodSnapshot(require_aware(initial_at), initial, initial, 0)

    @staticmethod
    def _snapshot(row):
        return MoodSnapshot(
            from_utc_iso(row["at"]),
            Mood(row["p"], row["a"], row["d"]),
            Mood(row["baseline_p"], row["baseline_a"], row["baseline_d"]),
            row["sleep_debt"] or 0,
            row["last_event"],
            row["trigger_id"],
        )

    def _latest(self, connection):
        row = connection.execute(
            "SELECT * FROM mood ORDER BY at DESC LIMIT 1"
        ).fetchone()
        return self._snapshot(row) if row else self._initial

    @staticmethod
    def _clear_due(connection, at, queue_id=None):
        rows = connection.execute(
            "SELECT id FROM mood_queue WHERE fire_at<=?", (to_utc_iso(at),)
        )
        if any(row[0] != queue_id for row in rows):
            raise PendingResolution("Consume due resolutions with record_event first")

    def view(self, at: datetime, context: BaselineContext) -> MoodSnapshot:
        at = require_aware(at)
        with self.database.connection() as connection:
            connection.execute("BEGIN")
            self._clear_due(connection, at)
            return self.model.decay(self._latest(connection), at, context)

    def pending_resolutions(self, until: datetime) -> list[Resolution]:
        with self.database.connection() as connection:
            return [
                Resolution(
                    row["id"],
                    from_utc_iso(row["fire_at"]),
                    row["event_id"],
                    row["trigger_id"],
                )
                for row in connection.execute(
                    "SELECT * FROM mood_queue WHERE fire_at<=? ORDER BY fire_at, id",
                    (to_utc_iso(until),),
                )
            ]

    def _change(
        self,
        connection,
        event_id,
        delta,
        at,
        context,
        *,
        pierce=False,
        trigger_id=None,
        queue_id=None,
    ):
        self._clear_due(connection, at, queue_id)
        state = self._latest(connection)
        existing = connection.execute("SELECT 1 FROM mood LIMIT 1").fetchone()
        if at.timestamp() < state.at.timestamp() or (
            existing and at.timestamp() == state.at.timestamp()
        ):
            raise ValueError("Mood mutations must be strictly chronological")
        decayed = self.model.decay(state, at, context)
        mood = self.model.changed(decayed, delta, pierce=pierce)
        insert_mood(
            connection,
            at=at,
            p=mood.P,
            a=mood.A,
            d=mood.D,
            baseline_p=decayed.baseline.P,
            baseline_a=decayed.baseline.A,
            baseline_d=decayed.baseline.D,
            octant=self.model.octant(mood),
            last_event=event_id,
            trigger_id=trigger_id,
            sleep_debt=context.sleep_debt,
        )
        log.info(
            "mood_event_recorded",
            event_id=event_id,
            trigger_id=trigger_id,
            event_at=to_utc_iso(at),
        )
        return self._latest(connection)

    def record_event(
        self,
        event_id: str,
        *,
        at: datetime,
        context: BaselineContext,
        queue_id: int | None = None,
    ) -> MoodSnapshot:
        at = require_aware(at)
        if queue_id is None and event_id not in self.model.events:
            raise KeyError(event_id)

        def save(connection):
            trigger_id = None
            if queue_id is None:
                delta = self.model.events[event_id]["delta"]
            else:
                row = connection.execute(
                    "SELECT * FROM mood_queue WHERE id=?", (queue_id,)
                ).fetchone()
                if row is None:
                    raise LookupError(
                        "Resolution was already consumed or does not exist"
                    )
                if (
                    row["event_id"] != event_id
                    or from_utc_iso(row["fire_at"]).timestamp() != at.timestamp()
                ):
                    raise ValueError("Resolution identity or time does not match")
                delta, trigger_id = json.loads(row["delta_json"]), row["trigger_id"]
            state = self._change(
                connection,
                event_id,
                delta,
                at,
                context,
                trigger_id=trigger_id,
                queue_id=queue_id,
            )
            if queue_id is not None:
                connection.execute("DELETE FROM mood_queue WHERE id=?", (queue_id,))
            return state

        return self.database.run_transaction(save)

    def fire_trigger(
        self,
        trigger_id: str,
        *,
        at: datetime,
        context: BaselineContext,
        week_start: datetime,
        next_exam_at: datetime | None,
        rng: Random,
    ) -> MoodSnapshot:
        at, week_start = require_aware(at), require_aware(week_start)
        week_end = week_start + timedelta(days=7)
        if not week_start.timestamp() <= at.timestamp() < week_end.timestamp():
            raise ValueError("Trigger time is outside the supplied week")
        trigger = self.model.triggers[trigger_id]
        rules = self.model.trigger_rules
        if (
            next_exam_at is not None
            and 0 <= elapsed_hours(at, next_exam_at) <= rules["never_before_exam_hours"]
        ):
            raise TriggerBlocked("Trigger is too close to an exam")
        resolution = None
        if outcomes := trigger.get("resolution"):
            roll, cumulative = rng.random(), 0.0
            for outcome in outcomes:
                cumulative += outcome["p"]
                if roll < cumulative:
                    resolution = outcome
                    break
            if resolution is None:
                raise UnspecifiedResolution(
                    "TODO(TRIGGER-POLICY): no outcome covers this probability"
                )

        def save(connection):
            previous = connection.execute(
                "SELECT at FROM mood WHERE trigger_id=last_event ORDER BY at DESC"
            ).fetchall()
            within_week = [
                row
                for row in previous
                if week_start.timestamp()
                <= from_utc_iso(row[0]).timestamp()
                < week_end.timestamp()
            ]
            if len(within_week) >= rules["max_per_week"]:
                raise TriggerBlocked("Weekly trigger limit reached")
            if previous:
                gap = elapsed_hours(from_utc_iso(previous[0][0]), at) / 24
                if (
                    gap < rules["cooldown_days"]
                    or gap < rules["min_gap_from_last_days"]
                ):
                    raise TriggerBlocked("Trigger cooldown has not elapsed")
            state = self._change(
                connection,
                trigger_id,
                trigger["delta"],
                at,
                context,
                pierce=trigger["pierce"],
                trigger_id=trigger_id,
            )
            if resolution:
                fire_at = add_elapsed(at, hours=resolution["after_hours"])
                connection.execute(
                    "INSERT INTO mood_queue(fire_at, event_id, delta_json, trigger_id) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        fire_at,
                        resolution["id"],
                        json.dumps([round(value, 4) for value in resolution["delta"]]),
                        trigger_id,
                    ),
                )
            return state

        return self.database.run_transaction(save)
