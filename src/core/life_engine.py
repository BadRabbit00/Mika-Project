"""Durable resources, dependent tasks and code-selected everyday consequences."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from random import Random

import structlog
from ruamel.yaml import YAML

from src.core.schedule import Schedule
from src.core.time_utils import from_utc_iso, require_aware, to_utc_iso

log = structlog.get_logger("blogai.life")


def packed(value):
    def rounded(item):
        if isinstance(item, dict):
            return {key: rounded(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [rounded(val) for val in item]
        return round(item, 4) if type(item) is float else item

    return json.dumps(
        rounded(value), ensure_ascii=False, allow_nan=False, sort_keys=True
    )


class LifeEngine:
    def __init__(self, database, config, config_dir):
        self.database, self.config = database, config
        self.rules = YAML(typ="safe").load(Path(config_dir) / "life_chains.yaml")
        self.life = YAML(typ="safe").load(Path(config_dir) / "life.yaml")
        self.schedule = Schedule.from_config(config_dir)

    @staticmethod
    def _state(c):
        row = c.execute(
            "SELECT value FROM life_state WHERE key='life.resources'"
        ).fetchone()
        if row is None:
            raise LookupError("Life resources have not been initialized")
        return json.loads(row[0])

    @staticmethod
    def _save(c, state, at):
        c.execute(
            "UPDATE life_state SET value=?,updated_at=? WHERE key='life.resources'",
            (packed(state), at),
        )

    def state(self):
        with self.database.connection(readonly=True) as c:
            return self._state(c)

    def public_state(self):
        state = self.state()
        keys = {
            "cash",
            "debt",
            "debt_due",
            "pantry",
            "coffee_stage",
            "episode",
            "coursework",
            "coursework_due",
            "cat_home",
            "ill_until",
            "gym_visits",
        }
        with self.database.connection(readonly=True) as c:
            events = [
                dict(at=row["at"], facts=json.loads(row["payload"])["facts"])
                for row in c.execute(
                    "SELECT at,payload FROM life_events WHERE kind!='need' ORDER BY "
                    "at DESC LIMIT 6"
                )
            ]
            pending = [
                dict(kind=row["kind"], reason=row["reason"], deadline=row["deadline"])
                for row in c.execute(
                    "SELECT kind,reason,deadline FROM life_tasks WHERE "
                    "status='pending' ORDER BY priority DESC LIMIT 5"
                )
            ]
        return {key: state[key] for key in keys} | {
            "recent_events": events,
            "commitments": pending,
        }

    def observe_weather(self, weather, at):
        at = require_aware(at)
        if weather is None or not 0 <= (at - weather.at).total_seconds() <= 3600:
            return

        def save(c):
            state = self._state(c)
            data = {
                key: round(value, 4) if type(value) is float else value
                for key, value in weather.context_data().items()
            }
            state["weather"] = {
                "facts": data,
                "at": to_utc_iso(at),
                "valid_until": to_utc_iso(at + timedelta(minutes=30)),
                "rain": weather.source != "seasonal"
                and bool(weather.precipitation_mm and weather.precipitation_mm > 0),
            }
            self._save(c, state, at)

        self.database.run_transaction(save)

    def bootstrap(self, at):
        at = require_aware(at)

        def save(c):
            state = dict(self.rules["initial"])
            state.update(
                cash=self.config["money"]["initial_cash"],
                housing=0,
                savings=self.config["money"]["initial_savings"],
                debt=self.config["money"]["initial_debt"],
                debt_due=None,
                pantry=self.config["food"]["initial_portions"],
                started_at=to_utc_iso(at),
                last_income_at=to_utc_iso(at),
                mother_extra={},
                npc={},
                last_activity=None,
                coursework_submitted=False,
                coursework_due=to_utc_iso(
                    at + timedelta(days=state["coursework_due_days"])
                ),
            )
            legacy = c.execute(
                "SELECT value FROM life_state WHERE key='series.episode'"
            ).fetchone()
            if legacy:
                state["episode"] = int(legacy[0])
            else:
                state["episode"] = self.life["progress"]["series"]["episode"]
            stage = c.execute(
                "SELECT value FROM life_state WHERE key='coffee_machine.stage'"
            ).fetchone()
            state["coffee_stage"] = [
                "leaking",
                "repaired",
                "broken",
                "broken",
                "working",
            ][int(stage[0]) if stage else 0]
            state["gym_active"] = True
            state["gym_visits"] = self.life["progress"]["gym"]["visits"]
            state["gym_expires"] = to_utc_iso(
                at + timedelta(weeks=self.life["progress"]["gym"]["expires_in_weeks"])
            )
            due_week = next(
                item["deadline_week"]
                for item in self.life["arcs"]
                if item["id"] == "coursework"
            )
            state["coursework_due"] = to_utc_iso(
                at.replace(
                    year=self.life["semester"]["start"].year,
                    month=self.life["semester"]["start"].month,
                    day=self.life["semester"]["start"].day,
                    hour=23,
                    minute=59,
                    second=0,
                    microsecond=0,
                )
                + timedelta(weeks=due_week - 1, days=6)
            )
            legacy_arcs = {
                row["id"]: dict(row) for row in c.execute("SELECT * FROM arcs")
            }
            state["imported_arcs"] = legacy_arcs
            state["imported_npc"] = {
                row["id"]: dict(row) for row in c.execute("SELECT * FROM npc")
            }
            if "coursework" in legacy_arcs:
                previous = legacy_arcs["coursework"]
                state["coursework"] = (
                    5 if previous["stage"] == 4 else 1 if previous["stage"] == 3 else 0
                )
                state["coursework_submitted"] = previous["stage"] == 4
                state["coursework_due"] = (
                    previous["deadline"] or state["coursework_due"]
                )
            if (
                "coffee_machine" in legacy_arcs
                and legacy_arcs["coffee_machine"]["stage"] is not None
            ):
                state["coffee_stage"] = [
                    "leaking",
                    "repaired",
                    "broken",
                    "broken",
                    "working",
                ][legacy_arcs["coffee_machine"]["stage"]]
            if "cat" in legacy_arcs:
                state["cat_home"] = legacy_arcs["cat"]["stage"] in {0, 1, 2}
            if "gym" in legacy_arcs and legacy_arcs["gym"]["stage"] == 3:
                state["gym_active"] = False
            c.execute(
                "INSERT OR IGNORE INTO life_state(key,value,updated_at) VALUES "
                "('life.resources',?,?)",
                (packed(state), at),
            )

        self.database.run_transaction(save)

    def tasks(self):
        with self.database.connection(readonly=True) as c:
            return [
                dict(row)
                for row in c.execute(
                    "SELECT * FROM life_tasks ORDER BY priority DESC,earliest_at,id"
                )
            ]

    def _person(self, state, person, at):
        previous = state["npc"].get(person)
        if previous is None:
            imported = state.get("imported_npc", {}).get(person)
            if imported and imported["state"] in {"calm", "tired", "strained"}:
                previous = {
                    "day": "",
                    "mood": imported["state"],
                    "busy": False,
                    "last_contact": imported["last_contact"],
                }
        day = str(at.date())
        if previous and previous["day"] == day:
            return previous
        rng = Random(f"{self.config['seed']}:{person}:{day}")
        # Persistence across days gives temperament memory without a fixed bad outcome.
        mood = (
            previous["mood"]
            if previous and rng.random() < 0.4
            else rng.choice(["calm", "calm", "tired", "strained"])
        )
        tension = state.get(person + "_tension", 0)
        if tension >= 2 and mood == "calm" and rng.random() < 0.35:
            mood = "tired"
        value = {
            "day": day,
            "mood": mood,
            "busy": rng.random() < 0.2,
            "last_contact": previous.get("last_contact") if previous else None,
        }
        state["npc"][person] = value
        return value

    def ready(self, task, at, activity):
        spec = json.loads(task["payload"])
        if self.state()["ill_until"] and task["kind"] not in {
            "recover",
            "groceries",
            "cat_care",
            "mother_contact",
            "mother_followup",
            "dasha_loan",
        }:
            return False
        if (
            task["status"] != "pending"
            or at < from_utc_iso(task["earliest_at"])
            or activity.task_id is not None
            or activity.kind in {"sleep", "travel", "class"}
            or activity.location not in spec["places"]
            or not spec["hours"][0] <= at.hour < spec["hours"][1]
            or spec.get("activities")
            and activity.kind not in spec["activities"]
            or activity.kind == "study"
            and "study" not in spec.get("activities", ())
        ):
            return False
        if (
            "cost" in spec
            and self.state()["cash"]
            < self.config["money"]["prices"][spec["cost"]]
            + self.config["money"]["essentials_reserve"]
        ):
            return False
        with self.database.connection(readonly=True) as c:
            return all(
                c.execute(
                    "SELECT 1 FROM life_tasks WHERE id=? AND status='completed'", (key,)
                ).fetchone()
                for key in json.loads(task["dependencies"])
            )

    def observe_person(self, person, at, *, mood, busy):
        at = require_aware(at)
        if mood not in {"calm", "tired", "strained"} or type(busy) is not bool:
            raise ValueError("Invalid simulated person observation")

        def save(c):
            state = self._state(c)
            value = self._person(state, person, at)
            value.update(mood=mood, busy=busy)
            self._save(c, state, at)

        self.database.run_transaction(save)

    def person(self, person, at):
        at = require_aware(at)

        def save(c):
            state = self._state(c)
            value = self._person(state, person, at)
            self._save(c, state, at)
            return value

        return self.database.run_transaction(save)

    def _task(self, c, kind, cause, at, *, after=0, dependencies=()):
        spec = self.rules["tasks"][kind]
        identity = kind + ":" + hashlib.sha256(cause.encode()).hexdigest()[:24]
        created = c.execute(
            "INSERT OR IGNORE INTO "
            "life_tasks(id,cause_id,kind,reason,priority,places,earliest_at,"
            "dependencies,payload,deadline) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                identity,
                cause,
                kind,
                spec["reason"],
                spec["priority"],
                packed(spec["places"]),
                at + timedelta(hours=after),
                packed(dependencies),
                packed(spec),
                from_utc_iso(self._state(c)["coursework_due"])
                if kind in {"coursework", "submit_coursework"}
                else at + timedelta(hours=after + 24)
                if kind == "brother_help"
                else None,
            ),
        )
        if created.rowcount:
            thread = c.execute(
                "INSERT INTO threads(opened_at,kind,text,status,priority) VALUES "
                "(?,'offtop',?,'open',?)",
                (
                    at,
                    packed(
                        {
                            "task": self.config["itinerary"]["task_labels"].get(
                                kind, kind
                            ),
                            "reason": spec["reason"],
                            "status": "pending",
                        }
                    ),
                    spec["priority"],
                ),
            ).lastrowid
            c.execute(
                "INSERT INTO life_state VALUES (?,?,?)",
                ("life.thread:" + identity, str(thread), at),
            )
        return identity

    def _event(
        self,
        c,
        state,
        identity,
        at,
        kind,
        facts,
        *,
        cause=None,
        task=None,
        activity=None,
        mood=None,
        changes=None,
        outcome=None,
        silent=False,
    ):
        existing = c.execute(
            "SELECT payload FROM life_events WHERE id=?", (identity,)
        ).fetchone()
        if existing:
            return json.loads(existing[0])
        payload = {
            "id": identity,
            "facts": facts,
            "outcome": outcome,
            "changes": changes or {},
            "location": activity.location if activity else None,
            "subject": activity.subject if activity else None,
            "origin": activity.origin if activity else None,
            "destination": activity.destination if activity else None,
            "at": to_utc_iso(at),
            "episode_before": state["episode"],
        }
        # Offline scenarios may supply an activity without a persisted whole day.
        activity_id = (
            activity.id
            if activity
            and c.execute(
                "SELECT 1 FROM life_activities WHERE id=?", (activity.id,)
            ).fetchone()
            else None
        )
        c.execute(
            "INSERT INTO "
            "life_events(id,entity,at,kind,payload,cause_id,task_id,activity_id,"
            "valid_until,publication_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                identity,
                identity,
                at,
                kind,
                packed(payload),
                cause,
                task,
                activity_id,
                activity.ends_at if activity else None,
                "silent" if silent else "pending",
            ),
        )
        if mood:
            c.execute(
                "INSERT INTO life_effects(id,event_id,kind,payload) VALUES "
                "(?,?,'mood',?)",
                (
                    identity + ":mood",
                    identity,
                    packed({"event": mood, "cause_at": to_utc_iso(at)}),
                ),
            )
        log.info("life_event_recorded", trace_id=identity, kind=kind, outcome=outcome)
        return payload

    def _money(self, c, state, identity, at, delta, operation, account="cash"):
        if type(delta) is not int or state[account] + delta < 0:
            raise ValueError("Money must be integer KZT and cannot overdraw cash")
        state[account] += delta
        c.execute(
            "INSERT INTO money_ledger(id,event_id,account,delta,balance_after,at) "
            "VALUES (?,?,?,?,?,?)",
            (identity + ":" + operation, identity, account, delta, state[account], at),
        )

    def needs(self, at=None):
        state = self.state()
        return {
            "cash": state["cash"],
            "economize": bool(at and self.shortfall(at, state) > 0),
            "groceries": state["pantry"] <= self.config["food"]["low_portions"],
            "ill": state["ill_until"] is not None,
            "rain": bool(
                at
                and state.get("weather", {}).get("rain")
                and at < from_utc_iso(state["weather"]["valid_until"])
            ),
            "gym": state["gym_active"] and not state["ill_until"],
            "urgent_deadline": state["coursework"] < 5
            and at is not None
            and from_utc_iso(state["coursework_due"]) - require_aware(at)
            < timedelta(days=2),
        }

    def notice(self, at):
        at = require_aware(at)

        def save(c):
            state = self._state(c)
            requirements = []
            for task in c.execute(
                "SELECT id,payload FROM life_tasks WHERE status='pending'"
            ).fetchall():
                if not self._matches(
                    state, json.loads(task["payload"]).get("when", {})
                ):
                    c.execute(
                        "UPDATE life_tasks SET status='cancelled' WHERE id=?",
                        (task["id"],),
                    )
                    thread = c.execute(
                        "SELECT value FROM life_state WHERE key=?",
                        ("life.thread:" + task["id"],),
                    ).fetchone()
                    if thread:
                        c.execute(
                            "UPDATE threads SET status='closed' WHERE id=?",
                            (int(thread[0]),),
                        )
            if self.shortfall(at, state) > 0:
                requirements.append(("mother_contact", "low_money"))
            if state["pantry"] <= self.config["food"]["low_portions"]:
                requirements.append(("groceries", "hunger"))
            if state["coursework"] < 5:
                requirements.append(("coursework", "deadline"))
            elif not state["coursework_submitted"]:
                requirements.append(("submit_coursework", None))
            if at >= from_utc_iso(state["gym_expires"]):
                state["gym_active"] = False
                if state["gym_visits"] >= 4:
                    requirements.append(("gym_renew", None))
            elif state["gym_active"]:
                requirements.append(("gym", None))
            if state["coffee_stage"] == "leaking":
                requirements.append(("coffee_repair", None))
            if state["cat_home"]:
                requirements.append(("cat_care", None))
            if (
                not state["ill_until"]
                and state["episode"] <= self.life["progress"]["series"]["total"]
            ):
                requirements.append(("series", None))
            for kind, fact in requirements:
                if (
                    kind == "mother_contact"
                    and c.execute(
                        "SELECT 1 FROM life_tasks WHERE kind IN "
                        "('mother_followup','dasha_loan') AND status='pending'"
                    ).fetchone()
                ):
                    continue
                if c.execute(
                    "SELECT 1 FROM life_tasks WHERE kind=? AND status IN "
                    "('pending','waiting','running')",
                    (kind,),
                ).fetchone():
                    continue
                identity = f"need:{at.date()}:{kind}"
                if c.execute(
                    "SELECT 1 FROM life_events WHERE id=?", (identity,)
                ).fetchone():
                    continue
                self._event(
                    c,
                    state,
                    identity,
                    at,
                    "need",
                    self.rules["facts"][fact]
                    if fact
                    else self.rules["tasks"][kind]["reason"],
                    silent=True,
                )
                self._task(c, kind, identity, at)
            self._save(c, state, at)

        self.database.run_transaction(save)

    @staticmethod
    def _matches(state, conditions):
        return all(
            (
                state[key] >= value["gte"]
                if isinstance(value, dict) and "gte" in value
                else state[key] == value
            )
            for key, value in conditions.items()
        )

    def _effects(self, state, effects, at):
        for key, value in effects.items():
            if key in {"cash", "debt", "savings"}:
                raise ValueError("Financial effects require the money ledger")
            if isinstance(value, dict):
                if "add" in value:
                    state[key] += value["add"]
                elif "after_hours" in value:
                    state[key] = to_utc_iso(at + timedelta(hours=value["after_hours"]))
                else:
                    raise ValueError("Unknown life effect")
            else:
                state[key] = value
        if state["pantry"] < 0:
            raise ValueError("Pantry cannot become negative")

    def execute(self, task_id, *, at, activity):
        at = require_aware(at)

        def save(c):
            task = c.execute(
                "SELECT * FROM life_tasks WHERE id=?", (task_id,)
            ).fetchone()
            if task is None:
                raise KeyError(task_id)
            identity = task_id + ":result"
            previous = c.execute(
                "SELECT payload FROM life_events WHERE id=?", (identity,)
            ).fetchone()
            if previous:
                return json.loads(previous[0])
            spec = json.loads(task["payload"])
            if (
                task["status"] != "pending"
                or at < from_utc_iso(task["earliest_at"])
                or activity.kind == "sleep"
                or activity.location not in spec["places"]
                or not spec["hours"][0] <= at.hour < spec["hours"][1]
                or spec.get("activities")
                and activity.kind not in spec["activities"]
            ):
                return None
            for dependency in json.loads(task["dependencies"]):
                if not c.execute(
                    "SELECT 1 FROM life_tasks WHERE id=? AND status='completed'",
                    (dependency,),
                ).fetchone():
                    return None
            state, kind = self._state(c), task["kind"]
            if activity.kind in {
                "class",
                "travel",
                "study",
            } and activity.kind not in spec.get("activities", ()):
                return None
            money, change, outcome = 0, {}, kind
            followup = spec.get("followup")
            mood, facts = spec.get("mood"), spec.get("facts")
            cfg = self.config["money"]
            if kind in {"mother_contact", "mother_followup"}:
                person = self._person(state, "mother", at)
                # A retry uses the next day's persisted availability.
                month = at.strftime("%Y-%m")
                used = state["mother_extra"].get(month, 0)
                amount = min(
                    cfg["mother_request_limit"],
                    cfg["mother_monthly_extra_limit"] - used,
                    self.shortfall(at, state),
                )
                outcome = (
                    "delay"
                    if person["busy"]
                    else "refusal"
                    if person["mood"] == "strained" or amount == 0
                    else "lecture"
                    if person["mood"] == "tired"
                    else "help"
                )
                mood = {
                    "delay": "life_uncertainty",
                    "refusal": "life_refusal",
                    "help": "life_relief",
                    "lecture": "life_mixed_help",
                }[outcome]
                if outcome in {"help", "lecture"}:
                    money = amount
                    state["mother_extra"][month] = used + amount
                    state["mother_tension"] += 1 if outcome == "lecture" else -1
                    state["expected_transfer"] = None
                else:
                    state["expected_transfer"] = (
                        {"requested": amount, "status": "unconfirmed"}
                        if outcome == "delay"
                        else None
                    )
                    followup = {
                        "kind": "mother_followup"
                        if outcome == "delay"
                        else "dasha_loan",
                        "hours": 24 if outcome == "delay" else 1,
                    }
                person["last_contact"] = to_utc_iso(at)
                facts = self.rules["facts"][outcome]
            elif kind == "dasha_loan":
                overdue = (
                    state["debt_due"]
                    and at >= from_utc_iso(state["debt_due"])
                    and state["debt"] > 0
                )
                money = (
                    0
                    if overdue or state["dasha_trust"] < 0
                    else max(0, cfg["loan_limit"] - state["debt"])
                )
                state["debt"] += money
                if money:
                    next_income = self._next_income(at)
                    state["debt_due"] = to_utc_iso(
                        min(next_income, at + timedelta(days=cfg["loan_max_days"]))
                    )
                outcome = "loan" if money else "no_loan"
                mood = "life_debt" if money else "life_refusal"
                facts = self.rules["facts"][outcome]
            elif kind == "groceries":
                money = -cfg["prices"]["groceries"]
                change = {"pantry": {"add": self.config["food"]["basket_portions"]}}
                mood, facts = "life_relief", self.rules["facts"]["groceries"]
            else:
                if "person" in spec:
                    person = self._person(state, spec["person"], at)
                    if person["busy"]:
                        c.execute(
                            "UPDATE life_tasks SET earliest_at=? WHERE id=?",
                            (at + timedelta(hours=24), task_id),
                        )
                        self._save(c, state, at)
                        return None
                    spec = spec["outcomes"][person["mood"]]
                    person["last_contact"] = to_utc_iso(at)
                    followup, mood, facts = (
                        spec.get("followup"),
                        spec.get("mood"),
                        spec["facts"],
                    )
                money = -cfg["prices"][spec["cost"]] if "cost" in spec else 0
                change = spec.get("effects", {})
            if state["cash"] + money < (
                cfg["essentials_reserve"] if kind == "coffee_replace" else 0
            ):
                return None
            event = self._event(
                c,
                state,
                identity,
                at,
                kind,
                facts,
                cause=task["cause_id"],
                task=task_id,
                activity=activity,
                mood=mood,
                changes=change | {"money": money},
                outcome=outcome,
            )
            self._effects(state, change, at)
            if money:
                self._money(c, state, identity, at, money, kind)
            if followup:
                self._task(
                    c,
                    followup["kind"],
                    identity,
                    at,
                    after=followup["hours"],
                    dependencies=(task_id,),
                )
            c.execute(
                "UPDATE life_tasks SET status='completed',completed_at=? WHERE id=?",
                (at, task_id),
            )
            thread = c.execute(
                "SELECT value FROM life_state WHERE key=?", ("life.thread:" + task_id,)
            ).fetchone()
            if thread:
                c.execute(
                    "UPDATE threads SET status='closed' WHERE id=?", (int(thread[0]),)
                )
            self._save(c, state, at)
            return event

        return self.database.run_transaction(save)

    def _next_income(self, at):
        day = at.replace(hour=9, minute=0, second=0, microsecond=0)
        while True:
            if day > at and day.day in {
                self.config["money"]["scholarship_day"],
                self.config["money"]["allowance_day"],
            }:
                return day.replace(hour=9, minute=0, second=0, microsecond=0)
            day += timedelta(days=1)

    def shortfall(self, at, state):
        days = max(1, (self._next_income(at).date() - at.date()).days)
        portions = max(0, days * 3 - state["pantry"])
        basket = self.config["food"]["basket_portions"]
        groceries = (
            (portions + basket - 1)
            // basket
            * self.config["money"]["prices"]["groceries"]
        )
        travel = (
            sum(
                bool(self.schedule.classes(at + timedelta(days=offset)))
                for offset in range(days)
            )
            * 2
            * self.config["money"]["prices"]["transport"]
        )
        essentials = groceries + travel + sum(state.get("unpaid_bills", {}).values())
        return max(
            0, max(essentials, self.config["money"]["low_balance"]) - state["cash"]
        )

    def income(self, at):
        at = require_aware(at)

        def save(c):
            state = self._state(c)
            cfg = self.config["money"]
            cursor = from_utc_iso(state["last_income_at"])
            if at <= cursor:
                return
            for index in range((at.date() - cursor.date()).days + 1):
                day = (cursor + timedelta(days=index)).replace(
                    hour=9, minute=0, second=0, microsecond=0
                )
                if not cursor < day <= at:
                    continue
                operations = []
                for key in ("scholarship", "allowance"):
                    if day.day == cfg[key + "_day"]:
                        operations.append((key, cfg[key]))
                if day.day == cfg["housing_day"]:
                    operations += [
                        ("housing_support", cfg["housing_support"]),
                        ("rent", -cfg["rent"]),
                        ("utilities", -cfg["utilities"]),
                    ]
                if day.day == cfg["mobile_day"]:
                    operations.append(("mobile", -cfg["mobile"]))
                if not operations:
                    continue
                identity = "income:" + str(day.date())
                if c.execute(
                    "SELECT 1 FROM life_events WHERE id=?", (identity,)
                ).fetchone():
                    continue
                self._event(
                    c, state, identity, day, "income", self.rules["facts"]["income"]
                )
                for name, delta in operations:
                    account = (
                        "housing"
                        if name in {"housing_support", "rent", "utilities"}
                        else "cash"
                    )
                    if state[account] + delta >= 0:
                        self._money(c, state, identity, day, delta, name, account)
                    else:
                        state.setdefault("unpaid_bills", {})[
                            name + ":" + str(day.date())
                        ] = -delta
                for bill, amount in list(state.get("unpaid_bills", {}).items()):
                    if state["cash"] >= amount + cfg["essentials_reserve"]:
                        self._money(c, state, identity, day, -amount, "arrears:" + bill)
                        del state["unpaid_bills"][bill]
                repayment = min(
                    state["debt"], max(0, state["cash"] - cfg["essentials_reserve"])
                )
                if repayment:
                    self._money(c, state, identity, day, -repayment, "repayment")
                    state["debt"] -= repayment
                    state["dasha_trust"] += int(state["debt"] == 0)
                    if state["debt"] == 0:
                        state["debt_due"] = None
            state["last_income_at"] = to_utc_iso(at)
            self._save(c, state, at)

        self.database.run_transaction(save)

    def activity(self, activity, at):
        """Observe actual activity once; never replay unobserved past activities."""
        at = require_aware(at)
        if activity.kind == "sleep":
            return None

        def save(c):
            identity = "activity:" + activity.id
            if c.execute(
                "SELECT 1 FROM life_events WHERE id=?", (identity,)
            ).fetchone():
                return None
            state = self._state(c)
            key = {
                "breakfast": "meal",
                "lunch": "meal",
                "dinner": "meal",
                "food_break": "meal",
                "travel": "journey",
            }.get(activity.kind, activity.kind)
            signature = [
                activity.kind,
                activity.location,
                activity.origin,
                activity.destination,
                activity.subject,
            ]
            if (
                state.get("last_activity_signature") == signature
                or activity.task_id is not None
            ):
                return None
            facts = self.rules["facts"].get(key, activity.label)
            money, mood = 0, None
            changes = {}
            if key == "meal":
                if state["pantry"]:
                    changes = {"pantry": {"add": -1}}
                    mood = "life_meal"
                else:
                    facts, mood = self.rules["facts"]["hunger"], "life_hunger"
            if key == "journey":
                if activity.label == self.config["itinerary"]["labels"]["travel"]:
                    cost = self.config["money"]["prices"]["transport"]
                    if state["cash"] < cost:
                        raise ValueError(
                            "A paid journey requires enough money before departure"
                        )
                    money = -cost
                else:
                    facts = self.rules["facts"]["walking_journey"]
            if key == "cafe":
                cost = self.config["money"]["prices"]["coffee"]
                if state["cash"] < cost:
                    facts = self.rules["facts"]["low_money"]
                else:
                    money, mood = -cost, "life_rest"
            if activity.kind in {"tea_break", "short_rest"}:
                mood = "life_rest"
            event = self._event(
                c,
                state,
                identity,
                at,
                "situation",
                facts,
                activity=activity,
                mood=mood,
                changes=changes | {"money": money},
            )
            self._effects(state, changes, at)
            if money:
                self._money(c, state, identity, at, money, key)
            state["last_activity"] = activity.id
            state["last_activity_signature"] = signature
            self._save(c, state, at)
            return event

        return self.database.run_transaction(save)

    def seed_stories(self, at, activity):
        at = require_aware(at)
        if (
            activity.location != "дом"
            or activity.kind != "rest"
            or activity.task_id is not None
        ):
            return

        def save(c):
            state = self._state(c)
            for name, spec in self.rules["seeds"].items():
                marker = f"story:{at.date()}:{name}"
                if c.execute(
                    "SELECT 1 FROM life_state WHERE key=?", (marker,)
                ).fetchone():
                    continue
                selected = (
                    self._matches(state, spec.get("when", {}))
                    and Random(f"{self.config['seed']}:{marker}").random()
                    < spec["chance"]
                )
                c.execute(
                    "INSERT INTO life_state(key,value,updated_at) VALUES (?,?,?)",
                    (marker, packed(selected), at),
                )
                followup = spec.get("followup")
                if not selected or (
                    followup
                    and c.execute(
                        "SELECT 1 FROM life_tasks WHERE kind=? AND status='pending'",
                        (followup["kind"],),
                    ).fetchone()
                ):
                    continue
                self._event(
                    c,
                    state,
                    marker,
                    at,
                    name,
                    spec["facts"],
                    activity=activity,
                    mood=spec.get("mood"),
                    changes=spec.get("effects", {}),
                )
                self._effects(state, spec.get("effects", {}), at)
                if followup:
                    self._task(c, followup["kind"], marker, at, after=followup["hours"])
            self._save(c, state, at)

        self.database.run_transaction(save)
