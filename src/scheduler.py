"""Almaty activity scheduling and generation admission, independent of the FSM."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from ruamel.yaml import YAML

from src.core.time_utils import (
    ALMATY,
    add_elapsed,
    clock_minute,
    elapsed_hours,
    from_utc_iso,
    in_clock_window,
    local_clock,
    now,
    require_aware,
    to_utc_iso,
)

log = structlog.get_logger("blogai.scheduler")


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    @property
    def key(self):
        return to_utc_iso(self.start)


@dataclass(frozen=True)
class Rhythm:
    weekday: tuple[tuple[str, int], ...]
    weekend: tuple[tuple[str, int], ...]
    quiet_hours: tuple[str, str]
    mu: float
    sigma: float
    min_min: float
    max_min: float
    max_events: int

    @classmethod
    def from_mapping(cls, data):
        if data["gap"]["dist"] != "lognormal":
            raise ValueError(
                "Only the specified lognormal gap distribution is supported"
            )
        sessions = data["sessions"]
        result = cls(
            tuple((row["start"], row["len_min"]) for row in sessions["weekday"]),
            tuple((row["start"], row["len_min"]) for row in sessions["weekend"]),
            tuple(data["quiet_hours"]),
            data["gap"]["mu"],
            data["gap"]["sigma"],
            data["gap"]["min_min"],
            data["gap"]["max_min"],
            data["max_events_per_session"],
        )
        if (
            not result.weekday
            or not result.weekend
            or len(result.quiet_hours) != 2
            or not 0 < result.min_min <= result.max_min
            or result.sigma < 0
            or type(result.max_events) is not int
            or result.max_events < 1
        ):
            raise ValueError("Invalid activity rhythm")
        for start, duration in (*result.weekday, *result.weekend):
            clock_minute(start)
            if type(duration) is not int or not 0 < duration <= 1440:
                raise ValueError("Invalid activity window duration")
        for clock in result.quiet_hours:
            clock_minute(clock)
        return result

    @classmethod
    def from_file(cls, path):
        path = Path(path)
        if not path.exists():
            raise ValueError("TODO(RHYTHM-CONFIG): an explicit rhythm file is required")
        return cls.from_mapping(YAML(typ="safe").load(path.read_text()))

    def gap_minutes(self, rng):
        return min(
            self.max_min, max(self.min_min, rng.lognormvariate(self.mu, self.sigma))
        )

    def windows(self, day):
        day = require_aware(day)
        slots = self.weekday if day.weekday() < 5 else self.weekend
        return tuple(
            Window(
                local_clock(day, start),
                add_elapsed(local_clock(day, start), minutes=duration),
            )
            for start, duration in sorted(slots)
        )

    def window(self, at):
        at = require_aware(at)
        if in_clock_window(at, *self.quiet_hours):
            return None
        return next(
            (
                window
                for day in (at - timedelta(days=1), at)
                for window in self.windows(day)
                if window.start <= at < window.end
            ),
            None,
        )

    def next_window(self, at):
        at = require_aware(at)
        active = self.window(at)
        if active:
            return active
        for offset in range(8):
            for window in self.windows(at + timedelta(days=offset)):
                if window.start >= at and not in_clock_window(
                    window.start, *self.quiet_hours
                ):
                    return window
        raise ValueError("The rhythm has no future activity window outside quiet hours")


class MemoryReservations:
    """Explicit process-local admission accounting for tests or ephemeral runs."""

    def __init__(self):
        self.slots = {}

    def reserve(self, action_id, window, at, limit):
        if self.slots.get(action_id) == window.key:
            return True
        if sum(key == window.key for key in self.slots.values()) >= limit:
            return False
        self.slots[action_id] = window.key
        return True


class SQLiteReservations:
    def __init__(self, database):
        self.database = database

    def reserve(self, action_id, window, at, limit):
        at = require_aware(at)

        def save(c):
            row = c.execute(
                "SELECT session_key FROM activity_reservations WHERE action_id=?",
                (action_id,),
            ).fetchone()
            if row and row[0] == window.key:
                return True
            used = c.execute(
                "SELECT count(*) FROM activity_reservations WHERE session_key=?",
                (window.key,),
            ).fetchone()[0]
            if used >= limit:
                return False
            c.execute(
                "INSERT INTO activity_reservations(action_id,session_key,at) "
                "VALUES (?,?,?) ON CONFLICT(action_id) DO UPDATE SET "
                "session_key=excluded.session_key,at=excluded.at",
                (action_id, window.key, at),
            )
            return True

        return self.database.run_transaction(save)


class ActivityScheduler:
    def __init__(self, rhythm, jobs, *, blackout, clock=now, reservations=None):
        self.rhythm, self.jobs, self.blackout, self.clock = (
            rhythm,
            jobs,
            blackout,
            clock,
        )
        self.reservations = reservations or MemoryReservations()
        self.scheduler = AsyncIOScheduler(
            timezone=ALMATY,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
            },
        )
        self._queued, self._sequence = set(), 0

    def start(self, *, paused=False):
        self.scheduler.start(paused=paused)

    def schedule(self, identity, work, *, at, trace_id):
        self.scheduler.add_job(
            self.enqueue,
            "date",
            run_date=require_aware(at),
            id=identity,
            replace_existing=True,
            args=(identity, work),
            kwargs={"trace_id": trace_id},
        )

    async def enqueue(self, identity, work, *, trace_id):
        at = require_aware(self.clock())
        window = self.rhythm.window(at)
        if identity in self._queued or window is None or self.blackout(at).blocked:
            log.info("generation_not_admitted", trace_id=trace_id, action_id=identity)
            return False
        self._queued.add(identity)
        try:
            reserved = await asyncio.to_thread(
                self.reservations.reserve, identity, window, at, self.rhythm.max_events
            )
        except BaseException:
            self._queued.discard(identity)
            raise
        if not reserved:
            self._queued.discard(identity)
            return False

        async def guarded():
            try:
                current = require_aware(self.clock())
                active = self.rhythm.window(current)
                if active is None or self.blackout(current).blocked:
                    log.info("generation_deferred_before_execution", trace_id=trace_id)
                    return
                if not await asyncio.to_thread(
                    self.reservations.reserve,
                    identity,
                    active,
                    current,
                    self.rhythm.max_events,
                ):
                    return
                await work()
            finally:
                self._queued.discard(identity)

        self._sequence += 1
        try:
            self.jobs.submit(
                trace_id, f"scheduled:{identity}:{self._sequence}", guarded
            )
        except asyncio.QueueFull:
            self._queued.discard(identity)
            return False
        return True

    async def close(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            await asyncio.sleep(0)


@dataclass(frozen=True)
class Thread:
    id: int
    kind: str
    opened_at: datetime
    priority: int = 0
    status: str = "open"

    def __post_init__(self):
        object.__setattr__(self, "opened_at", require_aware(self.opened_at))


def next_post_kind(threads, *, at, state, offtop_due, inbox_new):
    at = require_aware(at)
    active = sorted(
        (
            thread
            for thread in threads
            if thread.status == "open"
            and 0 <= elapsed_hours(thread.opened_at, at) <= 5 * 24
        ),
        key=lambda thread: (-thread.priority, thread.opened_at, thread.id),
    )
    if any(thread.kind == "correction" for thread in active):
        return "correction"
    confusion = next((thread for thread in active if thread.kind == "confusion"), None)
    if state == "WAITING" and confusion:
        return "struggle" if elapsed_hours(confusion.opened_at, at) >= 20 else None
    if state in {"SUMMARY_READY", "SUMMARY"}:
        return "summary"
    if offtop_due:
        return "offtop"
    if any(thread.kind == "question" for thread in active):
        return "answer"
    return "found" if inbox_new else None


def load_threads(database, *, at):
    at = require_aware(at)

    def snapshot(c):
        c.execute(
            "UPDATE threads SET status='stale' WHERE status='open' AND opened_at<?",
            (add_elapsed(at, hours=-5 * 24),),
        )
        return [
            Thread(
                row["id"],
                row["kind"],
                from_utc_iso(row["opened_at"]),
                row["priority"] or 0,
                row["status"],
            )
            for row in c.execute(
                "SELECT * FROM threads WHERE status='open' AND opened_at<=?", (at,)
            )
        ]

    return database.run_transaction(snapshot)
