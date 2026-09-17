"""Saved personal calendars and independent fictional NPC accounts."""

import json
from datetime import timedelta
from random import Random

from src.core.time_utils import from_utc_iso, local_clock, require_aware, to_utc_iso


class WorldPeople:
    def __init__(self, engine):
        self.engine, self.database = engine, engine.database
        self.config = engine.details

    def ensure(self, state, at):
        state.setdefault(
            "npc_accounts",
            {
                name: {"balance": spec["initial_cash"], "checked_at": to_utc_iso(at)}
                for name, spec in self.config["people"].items()
            },
        )

    def calendar(self, c, person, at):
        at = require_aware(at)
        row = c.execute(
            "SELECT payload FROM world_calendars WHERE person=? AND day=?",
            (person, str(at.date())),
        ).fetchone()
        if row:
            return json.loads(row[0])
        spec = self.config["people"][person]
        slots = []

        def add(start, end, label):
            if end > start:
                slots.append(
                    {
                        "starts_at": to_utc_iso(start),
                        "ends_at": to_utc_iso(end),
                        "label": label,
                        "busy": True,
                    }
                )

        midnight = local_clock(at, "00:00")
        bedtime = local_clock(at, spec["sleep"][0])
        add(
            bedtime if bedtime.hour < 12 else midnight,
            local_clock(at, spec["sleep"][1]),
            "sleep",
        )
        if bedtime.hour >= 12:
            add(bedtime, midnight + timedelta(days=1), "sleep")
        for start, end, label in spec["weekdays" if at.weekday() < 5 else "weekends"]:
            add(local_clock(at, start), local_clock(at, end), label)
        rng = Random(f"{self.engine.config['seed']}:calendar:{person}:{at.date()}")
        if rng.random() < self.config["calendar"]["optional_chance"]:
            for hour in rng.sample(list(range(10, 22)), 12):
                start = midnight + timedelta(hours=hour)
                end = start + timedelta(
                    minutes=rng.randint(*self.config["calendar"]["optional_minutes"])
                )
                if all(
                    end <= from_utc_iso(s["starts_at"])
                    or start >= from_utc_iso(s["ends_at"])
                    for s in slots
                ):
                    add(start, end, rng.choice(spec["optional"]))
                    break
        payload = {
            "person": person,
            "day": str(at.date()),
            "slots": sorted(slots, key=lambda s: s["starts_at"]),
        }
        c.execute(
            "INSERT INTO world_calendars VALUES (?,?,?)",
            (person, str(at.date()), json.dumps(payload, ensure_ascii=False)),
        )
        return payload

    def available(self, c, person, at, until=None):
        until = require_aware(until or at + timedelta(minutes=1))
        cursor = require_aware(at)
        while cursor.date() <= until.date():
            if any(
                at < from_utc_iso(s["ends_at"]) and until > from_utc_iso(s["starts_at"])
                for s in self.calendar(c, person, cursor)["slots"]
            ):
                return False
            cursor = local_clock(cursor, "00:00") + timedelta(days=1)
        return not c.execute(
            "SELECT 1 FROM world_appointments WHERE person=? "
            "AND status IN ('reserved','attended') "
            "AND starts_at<? AND ends_at>?",
            (person, until, at),
        ).fetchone()

    def day(self, person, at):
        return self.database.run_transaction(
            lambda c: (
                self.calendar(c, person, at)
                | {"balance": self.engine._state(c)["npc_accounts"][person]["balance"]}
            )
        )

    def occupation(self, c, person, at):
        for slot in self.calendar(c, person, at)["slots"]:
            if from_utc_iso(slot["starts_at"]) <= at < from_utc_iso(slot["ends_at"]):
                return slot["label"]
        if c.execute(
            "SELECT 1 FROM world_appointments WHERE person=? "
            "AND status IN ('reserved','attended') AND starts_at<=? AND ends_at>?",
            (person, at, at),
        ).fetchone():
            return "appointment"
        return "free"

    def move_money(self, c, state, person, amount, event, at, operation):
        account = state["npc_accounts"][person]
        if type(amount) is not int or account["balance"] + amount < 0:
            raise ValueError("NPC money cannot be negative or fractional")
        account["balance"] += amount
        c.execute(
            "INSERT INTO money_ledger VALUES (?,?,?,?,?,?)",
            (
                event + ":npc:" + person + ":" + operation,
                event,
                "npc:" + person,
                amount,
                account["balance"],
                at,
            ),
        )

    def advance(self, at):
        at = require_aware(at)

        def save(c):
            state = self.engine._state(c)
            for person, account in state["npc_accounts"].items():
                self.calendar(c, person, at)
                self.engine._contact_person(c, state, person, at)
                cfg = self.config["people"][person]
                cursor = from_utc_iso(account["checked_at"])
                day = local_clock(cursor, "09:00")
                if day <= cursor:
                    day += timedelta(days=1)
                while day <= at:
                    identity = f"npc-budget:{person}:{day.date()}"
                    income = cfg["income"] if day.day == cfg["income_day"] else 0
                    expense = min(cfg["daily_expenses"], account["balance"] + income)
                    self.engine._event(
                        c,
                        state,
                        identity,
                        day,
                        "npc_budget",
                        "",
                        silent=True,
                        changes={
                            "person": person,
                            "income": income,
                            "expense": expense,
                        },
                    )
                    if income:
                        self.move_money(
                            c, state, person, income, identity, day, "income"
                        )
                    if expense:
                        self.move_money(
                            c, state, person, -expense, identity, day, "expenses"
                        )
                    account["checked_at"] = to_utc_iso(day)
                    day += timedelta(days=1)
            self.engine._save(c, state, at)

        self.database.run_transaction(save)
