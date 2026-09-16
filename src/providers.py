"""Explicit live observations and database-backed world, sleep, and PAD providers."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from random import Random
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.core.mood import BaselineContext, MoodModel, MoodService
from src.core.pad import Mood
from src.core.schedule import SleepWindow
from src.core.sleep import SleepHistory
from src.core.time_utils import from_utc_iso, require_aware
from src.core.weather import WeatherClient
from src.core.world import World


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
    location: str = Field(min_length=1)
    observed_at: datetime
    valid_until: datetime
    road_roll: float = Field(ge=0, le=1)
    initial_mood: dict[str, float]
    initial_mood_at: datetime
    initial_sleep_debt: float = Field(ge=0)
    sleep: list[SleepInput] = Field(min_length=1)

    @field_validator("observed_at", "valid_until", "initial_mood_at")
    @classmethod
    def aware(cls, value):
        return require_aware(value)

    @model_validator(mode="after")
    def validate_observations(self):
        Mood(**self.initial_mood)
        if self.valid_until <= self.observed_at:
            raise ValueError("Observation validity must end after its timestamp")
        for item in self.sleep:
            SleepWindow(item.bedtime, item.wake)
        return self

    @classmethod
    def read(cls, path):
        path = Path(path)
        if not path.is_file():
            # TODO(LIVE-INITIAL-STATE): supply current observations for this deployment.
            raise ValueError("An explicit live world-state JSON file is required")
        return cls.model_validate_json(path.read_text())


class SleepProvider(Protocol):
    def current(self, at): ...


class MoodProvider(Protocol):
    async def current(self, at) -> Mood: ...


class WorldProvider(Protocol):
    async def current(self, at): ...


class StoredSleepProvider:
    def __init__(self, database, schedule, inputs_path):
        self.inputs_path = inputs_path
        inputs = LiveInputs.read(inputs_path)
        self.history = SleepHistory(
            database, schedule, initial_debt=inputs.initial_sleep_debt
        )

    def current(self, at):
        for item in LiveInputs.read(self.inputs_path).sleep:
            self.history.record(
                SleepWindow(item.bedtime, item.wake),
                planned_bedtime=item.planned_bedtime,
                reason=item.reason,
            )
        return self.history.current(at)


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

    def history(self, at):
        _, _, debt = self.sleep.current(at)
        schedule = self.sleep.history.schedule
        pressure = schedule._data["semester_pressure"]
        period = pressure["weeks"].get(schedule.semester_week(at))
        bias = (
            Mood(*pressure["midterm_mood" if period == "midterm" else "session_mood"])
            if period
            else Mood(0, 0, 0)
        )
        with self.database.connection() as c:
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


class ObservedWorldProvider:
    def __init__(self, world, sleep, inputs_path, weather):
        self.world, self.sleep, self.inputs_path, self.weather = (
            world,
            sleep,
            inputs_path,
            weather,
        )
        self.rng = Random()

    def _day(self, at):
        inputs = LiveInputs.read(self.inputs_path)
        if not inputs.observed_at <= at <= inputs.valid_until:
            raise ValueError(
                "Current world observations are not yet valid or have expired"
            )
        sleep, reason, debt = self.sleep.current(at)
        day = self.world.day_context(
            at,
            sleep=sleep,
            sleep_debt=debt,
            location=inputs.location,
            road_roll=inputs.road_roll,
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
            rng=self.rng,
        )
        return WorldObservation(current.day, current.wake_reason, weather)


class RuntimeProviders:
    def __init__(self, database, config_dir, inputs_path, settings):
        inputs = LiveInputs.read(inputs_path)
        self.model = MoodModel.from_config(config_dir, settings=settings)
        world = World.from_config(config_dir)
        self.sleep = StoredSleepProvider(database, world.schedule, inputs_path)
        self.mood = DatabaseMoodProvider(database, self.model, self.sleep, inputs)
        self.weather = WeatherClient.from_config(config_dir, settings=settings)
        self.world = ObservedWorldProvider(world, self.sleep, inputs_path, self.weather)

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
