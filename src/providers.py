"""Autonomous world, sleep, and PAD providers with temporary operator overrides."""

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from random import Random
from typing import Protocol

import structlog
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from ruamel.yaml import YAML

from src.core.itinerary import Itinerary
from src.core.life_engine import LifeEngine
from src.core.mood import BaselineContext, MoodModel, MoodService
from src.core.pad import Mood
from src.core.schedule import Blackout, SleepWindow
from src.core.sleep_planner import ScheduledSleepProvider as ScheduledSleepProvider
from src.core.time_utils import from_utc_iso, now, require_aware, to_utc_iso
from src.core.weather import WeatherClient
from src.core.world import World

log = structlog.get_logger("blogai.providers")


class SleepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bedtime: datetime
    wake: datetime
    planned_bedtime: datetime
    reason: str = Field(min_length=1)

    @field_validator("bedtime", "wake", "planned_bedtime")
    @classmethod
    def aware(cls, value):
        return require_aware(value)


class LiveInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_mood: dict[str, float]
    initial_mood_at: datetime
    initial_sleep_debt: float = Field(ge=0, allow_inf_nan=False)
    location: str | None = Field(default=None, min_length=1)
    observed_at: datetime | None = None
    valid_until: datetime | None = None
    road_roll: float | None = Field(default=None, ge=0, lt=1)
    sleep: list[SleepInput] | None = None
    comment: str | None = Field(
        default=None,
        exclude=True,
        validation_alias=AliasChoices("_comment", "_комментарий"),
    )

    @field_validator("observed_at", "valid_until", "initial_mood_at")
    @classmethod
    def aware(cls, value):
        return require_aware(value) if value is not None else None

    @model_validator(mode="after")
    def validate_observations(self):
        Mood(**self.initial_mood)
        override = any(
            value is not None for value in (self.location, self.road_roll, self.sleep)
        )
        if override and self.valid_until is None:
            raise ValueError("Temporary overrides require valid_until")
        if self.valid_until is not None and self.valid_until <= (
            self.observed_at or self.initial_mood_at
        ):
            raise ValueError("Override validity must end after its timestamp")
        for item in self.sleep or ():
            SleepWindow(item.bedtime, item.wake)
        return self

    def active(self, at):
        at = require_aware(at)
        return (
            self.valid_until is not None
            and (self.observed_at or self.initial_mood_at) <= at < self.valid_until
        )

    def snapshot(self):
        return dict(
            initial_mood={
                axis: round(value, 4) for axis, value in self.initial_mood.items()
            },
            initial_mood_at=to_utc_iso(self.initial_mood_at),
            initial_sleep_debt=round(self.initial_sleep_debt, 4),
        )

    @classmethod
    def read(cls, path=None, *, at=None):
        if path is None:
            return cls(
                initial_mood=dict(P=0, A=0, D=0),
                initial_mood_at=now() if at is None else at,
                initial_sleep_debt=0,
            )
        return cls.model_validate_json(Path(path).read_text())


class SleepProvider(Protocol):
    def current(self, at): ...


class MoodProvider(Protocol):
    async def current(self, at) -> Mood: ...


class WorldProvider(Protocol):
    async def current(self, at): ...


class DatabaseMoodProvider:
    def __init__(self, database, model, sleep, inputs):
        self.database, self.model, self.sleep = database, model, sleep
        self.service = MoodService(
            database,
            model,
            initial=Mood(**inputs.initial_mood),
            initial_at=inputs.initial_mood_at,
        )
        self.lock = asyncio.Lock()

    async def apply_life_effect(self, at):
        at = require_aware(at)
        async with self.lock:
            await asyncio.to_thread(self._current, at)
            context = await asyncio.to_thread(self.history, at)

            def save(c):
                row = c.execute(
                    "SELECT e.* FROM life_effects e JOIN life_events v ON "
                    "v.id=e.event_id "
                    "WHERE e.kind='mood' AND e.applied_at IS NULL AND v.at<=? ORDER "
                    "BY v.at,e.id LIMIT 1",
                    (at,),
                ).fetchone()
                latest = c.execute(
                    "SELECT at FROM mood ORDER BY at DESC LIMIT 1"
                ).fetchone()
                if row is None or latest and from_utc_iso(latest[0]) >= at:
                    return
                before = self.service.model.decay(
                    self.service._latest(c), at, context
                ).mood
                payload = json.loads(row["payload"])
                after = self.service.record_event(
                    payload["event"], at=at, context=context, connection=c
                ).mood
                payload.update(
                    before={
                        axis: round(getattr(before, axis), 4)
                        for axis in ("P", "A", "D")
                    },
                    after={axis: getattr(after, axis) for axis in ("P", "A", "D")},
                    before_label=self.model.mood_block(before),
                    after_label=self.model.mood_block(after),
                )
                c.execute(
                    "UPDATE life_effects SET payload=?,applied_at=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), at, row["id"]),
                )

            await asyncio.to_thread(self.database.run_transaction, save)

    def history(self, at, *, sleep_debt=None):
        debt = self.sleep.current(at)[2] if sleep_debt is None else sleep_debt
        schedule = self.sleep.history.schedule
        pressure = schedule._data["semester_pressure"]
        period = pressure["weeks"].get(schedule.semester_week(at))
        bias = (
            Mood(*pressure["midterm_mood" if period == "midterm" else "session_mood"])
            if period
            else Mood(0, 0, 0)
        )
        with self.database.connection(readonly=True) as c:
            events = [
                dict(row)
                for row in c.execute(
                    "SELECT event_json,state_json,at FROM learning_events WHERE "
                    "at<=? ORDER BY at DESC,rowid DESC",
                    (at,),
                )
            ]
            exam = c.execute(
                "SELECT at,reasoning FROM curator_log WHERE at<=? AND "
                "json_valid(reasoning) "
                "AND json_extract(reasoning,'$.phase')='grade' ORDER BY at DESC,id "
                "DESC LIMIT 1",
                (at,),
            ).fetchone()
            correction = c.execute(
                "SELECT at FROM curator_log WHERE kind='correction' AND at<=? ORDER "
                "BY at DESC LIMIT 1",
                (at,),
            ).fetchone()
        waiting_since = None
        for row in events:
            if json.loads(row["state_json"])["phase"] != "WAITING":
                break
            waiting_since = from_utc_iso(row["at"])
        streak = 0
        for row in events:
            event = json.loads(row["event_json"])
            if event["kind"] != "quiz_done":
                continue
            threshold = json.loads(row["state_json"])["quiz_threshold"]
            if event["total"] < 5 or event["answered"] / event["total"] < threshold:
                break
            streak += 1
        return BaselineContext.from_history(
            at=at,
            windows=self.model._data["baseline_windows"],
            sleep_debt=debt,
            waiting_since=waiting_since,
            quiz_streak=streak,
            pressure=bias,
            correction_at=from_utc_iso(correction[0]) if correction else None,
            exam=(
                from_utc_iso(exam[0]),
                {"pass": "passed", "fail": "failed"}[
                    json.loads(exam[1])["receipt"]["result"]["overall"]
                ],
            )
            if exam
            else None,
        )

    def planning_p(self, at, debt):
        with self.database.connection(readonly=True) as c:
            row = c.execute(
                "SELECT * FROM mood WHERE at<=? ORDER BY at DESC LIMIT 1", (at,)
            ).fetchone()
        state = self.service._snapshot(row) if row else self.service._initial
        if at < state.at:
            return state.mood.P
        return self.model.decay(state, at, self.history(at, sleep_debt=debt)).mood.P

    def _current(self, at):
        for resolution in self.service.pending_resolutions(at):
            self.service.record_event(
                resolution.event_id,
                at=resolution.fire_at,
                context=self.history(resolution.fire_at),
                queue_id=resolution.id,
            )
        return self.service.view(at, self.history(at)).mood

    async def current(self, at):
        at = require_aware(at)
        async with self.lock:
            return await asyncio.to_thread(self._current, at)

    async def settings_changed(self, key, *, at):
        if key == "mood.enabled" and not self.model.enabled:
            async with self.lock:
                context = await asyncio.to_thread(self.history, at)
                await asyncio.to_thread(
                    self.service.record_event,
                    "settings_disabled",
                    at=at,
                    context=context,
                )


@dataclass(frozen=True)
class WorldObservation:
    day: object
    wake_reason: str
    weather: object = None


class DerivedWorldProvider:
    def __init__(self, world, sleep, overrides, weather, itinerary=None, life=None):
        self.world, self.sleep, self.overrides, self.weather = (
            world,
            sleep,
            overrides,
            weather,
        )
        self.itinerary = itinerary
        self.life = life
        if itinerary is not None:
            self.world.location_provider = self._location

    def _location(self, at):
        inputs = self.overrides()
        if inputs.active(at) and inputs.location is not None:
            return inputs.location
        return self.itinerary.current(at).location

    def _day(self, at):
        inputs = self.overrides()
        active = inputs.active(at)
        sleep, reason, debt = self.sleep.current(at)
        activity = None
        if self.itinerary is not None:
            self.itinerary.ensure(
                at,
                self.sleep.plan(at.date(), at=at),
                self.sleep.plan(at.date() + timedelta(days=1), at=at),
                needs=self.life.needs(at) if self.life is not None else None,
            )
            activity = self.itinerary.current(at)
        day = self.world.day_context(
            at,
            sleep=sleep,
            sleep_debt=debt,
            location=inputs.location
            if active and inputs.location is not None
            else self.world.where(at, sleep=sleep),
            road_roll=inputs.road_roll
            if active and inputs.road_roll is not None
            else Random("road:" + at.date().isoformat()).random(),
        )
        if activity is not None:
            sleeping = sleep.contains(at) or activity.kind == "sleep"
            overridden = day.location != activity.location
            day = replace(
                day,
                activity_id=activity.id,
                activity_kind=activity.kind if not overridden else "override",
                activity_label=activity.label if not overridden else None,
                subject=activity.subject if not overridden else None,
                activity_until=activity.ends_at,
                busy=activity.busy,
                study_allowed=activity.can_study and not sleeping and not overridden,
                chat_allowed=not sleeping,
                blackout=Blackout(sleeping, "sleep" if sleeping else None),
            )
        return WorldObservation(day, reason)

    async def current(self, at):
        return await asyncio.to_thread(self._day, require_aware(at))

    async def with_weather(self, at, *, last_mention_at):
        current = await self.current(at)
        weather = await self.world.relevant_weather(
            at,
            client=self.weather,
            location=current.day.location,
            last_mention_at=last_mention_at,
            rng=Random("weather:" + require_aware(at).date().isoformat()),
        )
        return WorldObservation(current.day, current.wake_reason, weather)


class RuntimeProviders:
    def __init__(
        self, database, config_dir, inputs_path=None, settings=None, *, clock=now
    ):
        started_at = require_aware(clock())
        requested = LiveInputs.read(inputs_path, at=started_at)

        def validate_time(at, source):
            if at > started_at:
                raise ValueError(
                    f"{source}: {to_utc_iso(at)} is later than startup "
                    f"{to_utc_iso(started_at)}. Check the clock and initial_mood_at; "
                    "stored state is not reset by editing --world-state. "
                    "See mika-startup/ЗАПУСК.md."
                )

        def bootstrap(c):
            latest = c.execute(
                "SELECT at FROM mood ORDER BY at DESC LIMIT 1"
            ).fetchone()
            if latest:
                validate_time(from_utc_iso(latest["at"]), "Stored mood history")
            previous = c.execute(
                "SELECT value FROM life_state WHERE key='runtime.initial'"
            ).fetchone()
            if previous:
                initial = LiveInputs.model_validate_json(previous[0])
                validate_time(initial.initial_mood_at, "Stored runtime.initial")
                return initial
            initial = requested.snapshot()
            # Adopt an existing mood history instead of resetting an upgraded database.
            oldest = c.execute("SELECT * FROM mood ORDER BY at LIMIT 1").fetchone()
            if oldest:
                initial = dict(
                    initial_mood=dict(P=oldest["p"], A=oldest["a"], D=oldest["d"]),
                    initial_mood_at=oldest["at"],
                    initial_sleep_debt=oldest["sleep_debt"] or 0,
                )
            validate_time(from_utc_iso(initial["initial_mood_at"]), "Initial snapshot")
            c.execute(
                "INSERT INTO life_state(key,value,updated_at) "
                "VALUES ('runtime.initial',?,?)",
                (json.dumps(initial), from_utc_iso(initial["initial_mood_at"])),
            )
            log.info(
                "world_initial_state_saved",
                trace_id="world-bootstrap",
                source="history"
                if oldest
                else "file"
                if inputs_path
                else "neutral_defaults",
                initial_mood_at=initial["initial_mood_at"],
                initial_sleep_debt=initial["initial_sleep_debt"],
            )
            return LiveInputs.model_validate(initial)

        initial = database.run_transaction(bootstrap)
        overrides = (
            (lambda: LiveInputs.read(inputs_path)) if inputs_path else (lambda: initial)
        )
        self.model = MoodModel.from_config(config_dir, settings=settings)
        world = World.from_config(config_dir)
        self.life_config = YAML(typ="safe").load(
            (Path(config_dir) / "life_simulation.yaml").read_text(encoding="utf-8")
        )
        self.itinerary = Itinerary(database, world.schedule, self.life_config)
        self.life = LifeEngine(database, self.life_config, config_dir)
        self.life.bootstrap(started_at)
        world.locations = dict(world.locations) | {
            name: tuple(value["objects"])
            for name, value in self.life_config["itinerary"]["extra_locations"].items()
        }
        self.sleep = ScheduledSleepProvider(
            database, world.schedule, initial, overrides=overrides
        )
        self.mood = DatabaseMoodProvider(database, self.model, self.sleep, initial)
        self.sleep.mood_at = self.mood.planning_p
        self.weather = WeatherClient.from_config(config_dir, settings=settings)
        self.world = DerivedWorldProvider(
            world, self.sleep, overrides, self.weather, self.itinerary, self.life
        )

    async def context(self, at):
        observation = await self.world.current(at)
        return dict(
            day=observation.day,
            mood=await self.mood.current(at),
            wake_reason=observation.wake_reason,
        )

    async def blackout(self, at):
        return (await self.world.current(at)).day.blackout

    async def close(self):
        await self.weather.client.aclose()
