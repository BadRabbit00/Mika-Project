"""Daily deterministic sleep plans with immutable completed-night accounting."""

import json
from datetime import date, timedelta
from random import Random
from threading import RLock

import structlog

from src.core.schedule import SleepWindow
from src.core.sleep import SleepHistory
from src.core.time_utils import from_utc_iso, local_clock, require_aware, to_utc_iso

log = structlog.get_logger("blogai.sleep_planner")


class ScheduledSleepProvider:
    def __init__(self, database, schedule, initial, *, overrides):
        self.database, self.initial, self.overrides = database, initial, overrides
        self.history = SleepHistory(
            database, schedule, initial_debt=initial.initial_sleep_debt
        )
        self.mood_at = lambda at, debt: initial.initial_mood["P"]
        self.lock = RLock()
        self._adopt_history()

    def _adopt_history(self):
        # A wake date identifies a night even when jitter moves bedtime across midnight.
        def adopt(c):
            rows = [dict(row) for row in c.execute("SELECT * FROM sleep_log")]
            keys = [from_utc_iso(row["wake_at"]).date().isoformat() for row in rows]
            if len(set(keys)) != len(keys):
                raise ValueError("Sleep history has multiple nights on one wake date")
            if all(row["night"] == key for row, key in zip(rows, keys, strict=True)):
                return
            c.execute("DELETE FROM sleep_log")
            for row, key in zip(rows, keys, strict=True):
                row["night"] = key
                columns = ",".join(row)
                placeholders = ",".join("?" for _ in row)
                c.execute(
                    f"INSERT INTO sleep_log({columns}) VALUES ({placeholders})",
                    tuple(row.values()),
                )
            log.info("sleep_night_keys_normalized", count=len(rows))

        self.database.run_transaction(adopt)

    def _row(self, day):
        with self.database.connection(readonly=True) as c:
            row = c.execute("SELECT * FROM sleep_log WHERE night=?", (day,)).fetchone()
        return dict(row) if row else None

    def _facts(self, at):
        with self.database.connection(readonly=True) as c:
            events = [
                dict(row)
                for row in c.execute(
                    "SELECT event_json,state_json FROM learning_events WHERE at<=? "
                    "ORDER BY at DESC,rowid DESC",
                    (at,),
                )
            ]
            state = json.loads(events[0]["state_json"]) if events else {}
            complexity = c.execute(
                "SELECT complexity FROM sources WHERE ingested_at<=? "
                "ORDER BY ingested_at DESC,rowid DESC LIMIT 1",
                (at,),
            ).fetchone()
            debt = c.execute(
                "SELECT debt_after FROM sleep_log WHERE debt_applied=1 AND wake_at<=? "
                "ORDER BY wake_at DESC LIMIT 1",
                (at,),
            ).fetchone()
            fight = c.execute(
                "SELECT last_event FROM mood WHERE trigger_id='fight_with_boyfriend' "
                "AND at<=? ORDER BY at DESC LIMIT 1",
                (at,),
            ).fetchone()
        rounds = sum(
            json.loads(row["event_json"])["kind"] == "quiz_done"
            for row in events
            if json.loads(row["state_json"]).get("topic") == state.get("topic")
        )
        label = self.history.schedule.mood_label(
            learning_state=state.get("phase", "IDLE"),
            rounds=rounds,
            p=self.mood_at(at, debt[0] if debt else self.initial.initial_sleep_debt),
        )
        return (
            complexity[0] if complexity else None,
            label,
            {
                "not_fighting": fight is None or fight[0] == "made_up",
            },
        )

    def _save(self, sleep, *, reason, planned, origin, until=None):
        def save(c):
            night = sleep.wake.date().isoformat()
            previous = c.execute(
                "SELECT * FROM sleep_log WHERE night=?", (night,)
            ).fetchone()
            values = (
                to_utc_iso(planned),
                to_utc_iso(sleep.bedtime),
                to_utc_iso(sleep.wake),
                reason,
            )
            if previous and previous["debt_applied"]:
                if (
                    origin == "override"
                    and tuple(
                        previous[key]
                        for key in (
                            "planned_bedtime",
                            "actual_bedtime",
                            "wake_at",
                            "wake_reason",
                        )
                    )
                    != values
                ):
                    raise ValueError("A completed sleep night cannot be overwritten")
                return
            accounted = sleep.wake <= self.initial.initial_mood_at
            c.execute(
                "INSERT INTO sleep_log(night,planned_bedtime,actual_bedtime,wake_at,"
                "wake_reason,hours,debt_after,debt_applied,origin,override_until) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(night) DO UPDATE SET "
                "planned_bedtime=excluded.planned_bedtime,"
                "actual_bedtime=excluded.actual_bedtime,wake_at=excluded.wake_at,"
                "wake_reason=excluded.wake_reason,hours=excluded.hours,"
                "debt_after=excluded.debt_after,debt_applied=excluded.debt_applied,"
                "origin=excluded.origin,override_until=excluded.override_until",
                (
                    night,
                    *values,
                    round(sleep.hours, 4),
                    round(self.initial.initial_sleep_debt, 4) if accounted else 0,
                    int(accounted),
                    origin,
                    until,
                ),
            )
            if (
                previous is None
                or tuple(
                    previous[key]
                    for key in (
                        "planned_bedtime",
                        "actual_bedtime",
                        "wake_at",
                        "wake_reason",
                    )
                )
                != values
            ):
                log.info(
                    "sleep_plan_saved", night=night, origin=origin, wake_at=values[2]
                )

        self.database.run_transaction(save)

    def _ensure(self, day, at, *, only_if_started=False):
        row = self._row(day)
        if row and (
            row["debt_applied"]
            or row["origin"] != "override"
            or from_utc_iso(row["override_until"]) > at
        ):
            return
        local_day = at.replace(
            year=day.year,
            month=day.month,
            day=day.day,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        # Catch-up uses facts available before that night, never later events.
        reference = min(at, local_day)
        complexity, label, triggers = self._facts(reference)
        rng = Random("sleep:" + day.isoformat())
        bedtime = self.history.schedule.plan_bedtime(
            local_day - timedelta(days=1),
            last_complexity=complexity,
            mood=label,
            rng=rng,
        )
        wake = self.history.schedule.resolve_wake(
            local_day, rng=rng, trigger_states=triggers
        )
        if only_if_started and bedtime > at:
            return
        self._save(
            SleepWindow(bedtime, wake.at),
            reason=wake.reason,
            planned=bedtime,
            origin="scheduled",
        )

    def plan(self, day: date, *, at):
        """Persist a sleep interval without advancing the observation clock."""
        at = require_aware(at)
        if type(day) is not date:
            raise TypeError("A wake date is required")
        with self.lock:
            self._ensure(day, at)
            row = self._row(day)
            return SleepWindow(
                from_utc_iso(row["actual_bedtime"]), from_utc_iso(row["wake_at"])
            )

    def disturb_future_night(self, at, *, minutes, cause_id):
        """A saved illness may shorten an unstarted night, once per cause/night."""
        at = require_aware(at)
        if type(minutes) is not int or not 0 < minutes < 120:
            raise ValueError("A bounded whole-minute sleep interruption is required")
        with self.lock:

            def save(c):
                rows = c.execute(
                    "SELECT * FROM sleep_log WHERE actual_bedtime>? AND "
                    "debt_applied=0 AND origin='scheduled' ORDER BY night LIMIT 1",
                    (at,),
                ).fetchall()
                for row in rows:
                    key = f"sleep-effect:{cause_id}:{row['night']}"
                    if c.execute(
                        "SELECT 1 FROM life_state WHERE key=?", (key,)
                    ).fetchone():
                        continue
                    bedtime = from_utc_iso(row["actual_bedtime"])
                    wake = from_utc_iso(row["wake_at"]) - timedelta(minutes=minutes)
                    if wake <= bedtime:
                        continue
                    c.execute(
                        "UPDATE sleep_log SET wake_at=?,wake_reason='illness',hours=? "
                        "WHERE night=?",
                        (
                            wake,
                            round(SleepWindow(bedtime, wake).hours, 4),
                            row["night"],
                        ),
                    )
                    c.execute(
                        "INSERT INTO life_state VALUES (?,?,?)",
                        (key, json.dumps({"minutes": minutes}), at),
                    )

            self.database.run_transaction(save)

    def current(self, at):
        at = require_aware(at)
        with self.lock:
            inputs = self.overrides()
            if inputs.active(at):
                for item in inputs.sleep or ():
                    self._save(
                        SleepWindow(item.bedtime, item.wake),
                        reason=item.reason,
                        planned=item.planned_bedtime,
                        origin="override",
                        until=inputs.valid_until,
                    )
            with self.database.connection(readonly=True) as c:
                latest = c.execute(
                    "SELECT max(night) FROM sleep_log "
                    "WHERE night<=? AND (origin!='override' OR debt_applied=1)",
                    (at.date(),),
                ).fetchone()[0]
                expired = [
                    date.fromisoformat(row[0])
                    for row in c.execute(
                        "SELECT night FROM sleep_log WHERE origin='override' "
                        "AND debt_applied=0 AND override_until<=?",
                        (at,),
                    )
                ]
            for day in expired:
                self.database.run_transaction(
                    lambda c, day=day: c.execute(
                        "DELETE FROM sleep_log WHERE night=? AND origin='override' "
                        "AND debt_applied=0 AND override_until<=?",
                        (day, at),
                    )
                )
                log.info("sleep_override_expired", night=day.isoformat())
            start = (
                min(
                    at.date() - timedelta(days=1),
                    self.initial.initial_mood_at.date() - timedelta(days=1),
                )
                if latest is None
                else date.fromisoformat(latest)
            )
            target = at.date()
            if at >= local_clock(
                at, self.history.schedule._data["day_shape"]["night_window"]["from"]
            ):
                target += timedelta(days=1)
            while start <= target:
                self._ensure(start, at)
                row = self._row(start)
                if from_utc_iso(row["wake_at"]) <= at:
                    self.history.complete(start, at=at)
                start += timedelta(days=1)
            if target == at.date():
                self._ensure(target + timedelta(days=1), at, only_if_started=True)
            return self.history.current(at)
