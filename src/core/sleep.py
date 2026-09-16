"""Durable sleep intervals and exactly-once local debt accounting."""

from datetime import date

import structlog

from src.core.pad import finite
from src.core.schedule import SleepWindow
from src.core.time_utils import from_utc_iso, require_aware, to_utc_iso

log = structlog.get_logger("blogai.sleep")


class SleepHistory:
    def __init__(self, database, schedule, *, initial_debt):
        if finite(initial_debt) < 0:
            raise ValueError("Initial sleep debt must be nonnegative")
        self.database, self.schedule = database, schedule
        self.initial_debt = initial_debt

    def record(self, sleep: SleepWindow, *, planned_bedtime, reason: str):
        planned_bedtime = require_aware(planned_bedtime)
        if not reason:
            raise ValueError("A wake reason is required")
        values = (
            sleep.bedtime.date().isoformat(),
            to_utc_iso(planned_bedtime),
            to_utc_iso(sleep.bedtime),
            to_utc_iso(sleep.wake),
            reason,
            round(sleep.hours, 4),
        )

        def save(c):
            previous = c.execute(
                "SELECT night,planned_bedtime,actual_bedtime,wake_at,wake_reason,hours "
                "FROM sleep_log WHERE night=?",
                (values[0],),
            ).fetchone()
            if previous:
                if tuple(previous) != values:
                    raise ValueError(
                        "A sleep night already exists with different facts"
                    )
                return
            c.execute(
                "INSERT INTO sleep_log(night,planned_bedtime,actual_bedtime,"
                "wake_at,wake_reason,hours,debt_after) VALUES (?,?,?,?,?,?,0)",
                values,
            )

        self.database.run_transaction(save)
        log.info("sleep_recorded", night=values[0], wake_at=values[3], reason=reason)

    def complete(self, night: date, *, at) -> float:
        at = require_aware(at)
        if type(night) is not date:
            raise ValueError("A sleep night must be a calendar date")

        def apply(c):
            row = c.execute(
                "SELECT * FROM sleep_log WHERE night=?", (night,)
            ).fetchone()
            if row is None:
                raise ValueError("Unknown sleep night")
            if at.timestamp() < from_utc_iso(row["wake_at"]).timestamp():
                raise ValueError("Cannot apply sleep debt before wake time")
            if row["debt_applied"]:
                return row["debt_after"]
            latest = c.execute(
                "SELECT * FROM sleep_log WHERE debt_applied=1 "
                "ORDER BY wake_at DESC LIMIT 1"
            ).fetchone()
            pending = c.execute(
                "SELECT * FROM sleep_log WHERE debt_applied=0 AND wake_at<=? "
                "ORDER BY wake_at",
                (row["wake_at"],),
            ).fetchall()
            if latest and latest["wake_at"] >= pending[0]["wake_at"]:
                raise ValueError("Sleep debt must be applied in chronological order")
            debt = latest["debt_after"] if latest else self.initial_debt
            for item in pending:
                sleep = SleepWindow(
                    from_utc_iso(item["actual_bedtime"]), from_utc_iso(item["wake_at"])
                )
                debt = round(self.schedule.sleep_debt(debt, sleep), 4)
                c.execute(
                    "UPDATE sleep_log SET debt_after=?,debt_applied=1 WHERE night=?",
                    (debt, item["night"]),
                )
                log.info("sleep_debt_applied", night=item["night"], debt=debt)
            return debt

        return self.database.run_transaction(apply)

    def current(self, at):
        at = require_aware(at)
        with self.database.connection() as c:
            row = c.execute(
                "SELECT * FROM sleep_log WHERE actual_bedtime<=? "
                "ORDER BY actual_bedtime DESC LIMIT 1",
                (at,),
            ).fetchone()
        if row is None:
            raise ValueError("No sleep history covers this date")
        sleep = SleepWindow(
            from_utc_iso(row["actual_bedtime"]), from_utc_iso(row["wake_at"])
        )
        if at.timestamp() >= sleep.wake.timestamp():
            debt = self.complete(date.fromisoformat(row["night"]), at=at)
        else:
            with self.database.connection() as c:
                previous = c.execute(
                    "SELECT debt_after FROM sleep_log WHERE debt_applied=1 "
                    "AND wake_at<=? ORDER BY wake_at DESC LIMIT 1",
                    (at,),
                ).fetchone()
                debt = previous[0] if previous else self.initial_debt
        return sleep, row["wake_reason"], debt
