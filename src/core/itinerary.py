"""Persisted local days with explicit journeys and immutable past activities."""

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from random import Random

import structlog

from src.core.time_utils import from_utc_iso, local_clock, require_aware, to_utc_iso

log = structlog.get_logger("blogai.itinerary")


@dataclass(frozen=True)
class Activity:
    id: str
    day: str
    starts_at: datetime
    ends_at: datetime
    location: str
    kind: str
    label: str
    subject: str | None = None
    origin: str | None = None
    destination: str | None = None
    task_id: str | None = None
    revision: int = 0

    def __post_init__(self):
        for name in ("starts_at", "ends_at"):
            object.__setattr__(self, name, require_aware(getattr(self, name)))
        if self.ends_at <= self.starts_at:
            raise ValueError("Activity end must follow start")
        if self.kind == "travel" and (
            not self.origin or not self.destination or self.origin == self.destination
        ):
            raise ValueError("Travel requires distinct endpoints")
        if self.kind == "study" and self.location != "дом":
            raise ValueError("Study requires home")

    @property
    def can_study(self):
        return self.kind == "study" and self.location == "дом" and self.task_id is None

    @property
    def can_chat(self):
        return self.kind != "sleep"

    @property
    def can_publish(self):
        return self.kind != "sleep"

    @property
    def busy(self):
        return self.task_id is not None or self.kind in {
            "class",
            "travel",
            "prepare",
            "shop",
            "gym",
            "walk",
            "study",
        }

    @classmethod
    def from_row(cls, row):
        values = {name: row[name] for name in cls.__dataclass_fields__}
        for name in ("starts_at", "ends_at"):
            values[name] = from_utc_iso(values[name])
        return cls(**values)


def _continuous(items):
    for left, right in zip(items, items[1:], strict=False):
        if left.ends_at != right.starts_at:
            raise ValueError("Activities must cover a continuous interval")
        location = left.destination if left.kind == "travel" else left.location
        origin = right.origin if right.kind == "travel" else right.location
        if location != origin:
            raise ValueError("Activity location requires a connecting journey")


class Itinerary:
    def __init__(self, database, schedule, config, *, transitions=None):
        self.database, self.schedule, self.config = database, schedule, config
        self.transitions = transitions

    def day(self, at):
        key = require_aware(at).date().isoformat()
        with self.database.connection(readonly=True) as connection:
            return tuple(
                Activity.from_row(row)
                for row in connection.execute(
                    "SELECT * FROM life_activities WHERE day=? AND state='active' "
                    "ORDER BY starts_at",
                    (key,),
                )
            )

    def current(self, at):
        at = require_aware(at)
        with self.database.connection(readonly=True) as connection:
            row = connection.execute(
                "SELECT * FROM life_activities WHERE state='active' "
                "AND starts_at<=? AND ends_at>?",
                (at, at),
            ).fetchone()
        if row is None:
            raise LookupError("No persisted activity covers this instant")
        return Activity.from_row(row)

    @staticmethod
    def _insert(connection, item):
        connection.execute(
            "INSERT INTO life_activities(id,day,starts_at,ends_at,location,kind,"
            "label,subject,origin,destination,task_id,revision,can_publish,can_chat,"
            "can_study) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item.id,
                item.day,
                item.starts_at,
                item.ends_at,
                item.location,
                item.kind,
                item.label,
                item.subject,
                item.origin,
                item.destination,
                item.task_id,
                item.revision,
                int(item.can_publish),
                int(item.can_chat),
                int(item.can_study),
            ),
        )

    def ensure(self, at, sleep, following_sleep, *, needs=None):
        at = require_aware(at)
        if existing := self.day(at):
            return existing
        items = self.build(at, sleep, following_sleep, needs=needs or {})
        _continuous(items)
        day = at.date().isoformat()

        def save(connection):
            if connection.execute(
                "SELECT 1 FROM life_days WHERE day=?", (day,)
            ).fetchone():
                return
            connection.execute(
                "INSERT INTO life_days(day,seed,config_json,created_at) VALUES "
                "(?,?,?,?)",
                (
                    day,
                    self.config["seed"],
                    json.dumps(self.config, ensure_ascii=False),
                    at,
                ),
            )
            for item in items:
                self._insert(connection, item)
            log.info(
                "life_day_planned",
                day=day,
                activities=len(items),
                trace_id=f"life:{day}",
            )

        self.database.run_transaction(save)
        return self.day(at)

    def build(self, at, sleep, following_sleep, *, needs):
        start = local_clock(at, "00:00")
        end = start + timedelta(days=1)
        cfg = self.config["itinerary"]
        places = cfg["locations"]
        home, university = places["home"], places["university"]
        seed = f"{self.config['seed']}:{start.date()}"
        rng = Random(seed)
        awake_end = min(end, following_sleep.bedtime)
        items, cursor = [], start

        def until(target, location, kind, **fields):
            nonlocal cursor
            target = min(target, end)
            if target <= cursor:
                return
            items.append(
                Activity(
                    f"{start.date()}:{len(items)}",
                    start.date().isoformat(),
                    cursor,
                    target,
                    location,
                    kind,
                    fields.pop("label", cfg["labels"][kind]),
                    **fields,
                )
            )
            cursor = target

        def minutes(count, location, kind, **fields):
            until(
                min(cursor + timedelta(minutes=count), awake_end),
                location,
                kind,
                **fields,
            )

        def journey(destination, duration, origin):
            minutes(
                duration,
                places["road"],
                "travel",
                origin=origin,
                destination=destination,
                label=cfg["labels"][
                    "walk_travel"
                    if university not in {origin, destination} or walking
                    else "travel"
                ],
            )

        until(sleep.bedtime, home, "rest")
        until(sleep.wake, home, "sleep")
        lessons = () if needs.get("ill") else self.schedule.classes(start)
        walking = (
            needs.get("cash", self.config["money"]["initial_cash"])
            < 2 * self.config["money"]["prices"]["transport"]
        )
        commute = (
            cfg["walk_university_minutes"]
            if walking
            else self.schedule._data["commute_minutes"]
        )
        if lessons and cursor < lessons[-1].end:
            departure = lessons[0].start - timedelta(
                minutes=commute + cfg["arrival_buffer_minutes"]
            )
            available = max(0, (departure - cursor).total_seconds() / 60)
            minimum = cfg["minimum_preparation_minutes"]
            breakfast = min(cfg["breakfast_minutes"], max(0, available - minimum))
            minutes(breakfast, home, "breakfast")
            until(max(departure, cursor + timedelta(minutes=minimum)), home, "prepare")
            journey(university, commute, home)
            for lesson in lessons:
                until(lesson.start, university, "break")
                until(lesson.end, university, "class", subject=lesson.subject)
            journey(home, commute, university)
        else:
            minutes(cfg["breakfast_minutes"], home, "breakfast")
        minutes(cfg["lunch_minutes"], home, "lunch") if lessons else until(
            min(local_clock(start, "13:00"), awake_end), home, "rest"
        )
        if not lessons:
            minutes(cfg["lunch_minutes"], home, "lunch")
        outing = None
        cash = needs.get("cash", self.config["money"]["initial_cash"])
        if not needs.get("ill") and not needs.get("urgent_deadline"):
            if (
                needs.get("groceries")
                and cash >= self.config["money"]["prices"]["groceries"]
            ):
                outing = "shop"
            elif rng.random() < cfg["outing_chance"]:
                outing = rng.choice(
                    (["walk", "cafe", "gym"] if needs.get("gym") else ["walk", "cafe"])
                    if cash >= self.config["money"]["prices"]["coffee"]
                    and not needs.get("economize")
                    else ["walk"]
                )
        if outing:
            place = "park" if outing == "walk" else outing
            duration = cfg["travel_minutes"]["home_" + place]
            begins = max(cursor, local_clock(start, cfg["outing_start"]))
            finishes = begins + timedelta(
                minutes=2 * duration + cfg["outing_minutes"][outing]
            )
            if finishes < min(awake_end, local_clock(start, cfg["dinner_start"])):
                until(begins, home, "rest")
                journey(places[place], duration, home)
                minutes(cfg["outing_minutes"][outing], places[place], outing)
                journey(home, duration, places[place])
        study_start = local_clock(
            start, self.schedule._data["day_shape"]["study_window"]["from"]
        )
        until(min(study_start, awake_end), home, "rest")
        studying = "rest" if needs.get("ill") else "study"
        until(min(local_clock(start, cfg["dinner_start"]), awake_end), home, studying)
        minutes(cfg["dinner_minutes"], home, "dinner")
        until(
            min(local_clock(start, cfg["evening_rest_start"]), awake_end),
            home,
            studying,
        )
        until(awake_end, home, "rest")
        until(end, home, "sleep")
        if self.transitions:
            for index in range(len(items) - 1, 0, -1):
                if items[index].kind != "sleep":
                    continue
                before = items[index - 1]
                begins = max(
                    before.starts_at,
                    before.ends_at
                    - timedelta(minutes=self.transitions["wind_down_minutes"]),
                )
                winding_down = replace(
                    before,
                    id=before.id + ":wind-down",
                    starts_at=begins,
                    kind="wind_down",
                    label=self.transitions["labels"]["wind_down"],
                )
                items[index - 1 : index] = (
                    [replace(before, ends_at=begins), winding_down]
                    if begins > before.starts_at
                    else [winding_down]
                )
        return tuple(items)

    def revise(self, at, replacements, *, cause_id, connection=None):
        """Revise an uncompleted suffix without moving Mika between locations."""
        at = require_aware(at)
        replacements = tuple(replacements)
        if not replacements or replacements[0].starts_at != at or not cause_id:
            raise ValueError("A revision requires its cause and exact start")
        _continuous(replacements)
        receipt = "itinerary.revision:" + cause_id

        def save(connection):
            if connection.execute(
                "SELECT 1 FROM life_state WHERE key=?", (receipt,)
            ).fetchone():
                return
            current = connection.execute(
                "SELECT * FROM life_activities WHERE state='active' "
                "AND starts_at<=? AND ends_at>?",
                (at, at),
            ).fetchone()
            if current is None:
                raise ValueError("No activity exists at revision time")
            if current["kind"] == "travel":
                raise ValueError("A journey must finish before changing location")
            first = replacements[0]
            location = first.origin if first.kind == "travel" else first.location
            if location != current["location"]:
                raise ValueError("A revision cannot change the current location")
            horizon = connection.execute(
                "SELECT max(ends_at) FROM life_activities WHERE day=? AND "
                "state='active'",
                (current["day"],),
            ).fetchone()[0]
            if replacements[-1].ends_at != from_utc_iso(horizon):
                raise ValueError("A revision must preserve the day's coverage")
            if from_utc_iso(current["starts_at"]) < at:
                connection.execute(
                    "UPDATE life_activities SET ends_at=? WHERE id=?",
                    (at, current["id"]),
                )
            connection.execute(
                "UPDATE life_activities SET state='cancelled' WHERE day=? "
                "AND starts_at>=? AND state='active'",
                (current["day"], at),
            )
            for item in replacements:
                if item.day != current["day"]:
                    raise ValueError("A revision must stay within its planned day")
                self._insert(connection, item)
            connection.execute(
                "INSERT INTO life_state(key,value,updated_at) VALUES (?,?,?)",
                (receipt, json.dumps([item.id for item in replacements]), at),
            )
            log.info("life_day_revised", trace_id=cause_id, at=to_utc_iso(at))

        if connection is None:
            self.database.run_transaction(save)
        else:
            save(connection)

    def adapt(self, at, *, needs, cause_id):
        """Revise future choices in place; finish a journey before replanning."""
        at = require_aware(at)
        current = self.current(at)
        if current.kind in {"sleep", "travel"}:
            return False
        day = self.day(at)
        suffix = [
            replace(item, starts_at=max(at, item.starts_at))
            for item in day
            if item.ends_at > at
        ]
        cfg = self.config["itinerary"]
        home = cfg["locations"]["home"]
        illness = needs.get("ill")
        rain = needs.get("rain")
        low_cash = (
            needs.get("economize")
            or needs.get("cash", 0) < self.config["money"]["prices"]["coffee"]
        )
        if (illness or rain and current.location == cfg["locations"]["park"]) and any(
            item.kind
            not in {"rest", "sleep", "breakfast", "lunch", "dinner", "wind_down"}
            for item in suffix
        ):
            first_sleep = next(
                (item.starts_at for item in suffix if item.kind == "sleep"),
                suffix[-1].ends_at,
            )
            revised, cursor = [], at
            if current.location != home:
                if current.location == cfg["locations"]["university"]:
                    duration = self.schedule._data["commute_minutes"]
                else:
                    place = next(
                        key
                        for key, value in cfg["locations"].items()
                        if value == current.location
                    )
                    duration = cfg["travel_minutes"]["home_" + place]
                cursor = min(first_sleep, at + timedelta(minutes=duration))
                if cursor > at:
                    revised.append(
                        replace(
                            current,
                            starts_at=at,
                            ends_at=cursor,
                            kind="travel",
                            label=cfg["labels"][
                                "travel"
                                if current.location == cfg["locations"]["university"]
                                else "walk_travel"
                            ],
                            location=cfg["locations"]["road"],
                            origin=current.location,
                            destination=home,
                            subject=None,
                        )
                    )
            for meal in suffix:
                if meal.kind not in {"breakfast", "lunch", "dinner", "wind_down"}:
                    continue
                begins = max(cursor, meal.starts_at)
                finishes = min(first_sleep, meal.ends_at)
                if finishes <= begins:
                    continue
                if cursor < begins:
                    revised.append(
                        replace(
                            current,
                            starts_at=cursor,
                            ends_at=begins,
                            kind="rest",
                            location=home,
                            label=cfg["labels"]["rest"],
                            subject=None,
                            task_id=None,
                        )
                    )
                revised.append(
                    replace(meal, starts_at=begins, ends_at=finishes, task_id=None)
                )
                cursor = finishes
            if cursor < first_sleep:
                revised.append(
                    replace(
                        current,
                        starts_at=cursor,
                        ends_at=first_sleep,
                        kind="rest",
                        location=home,
                        label=cfg["labels"]["rest"],
                        subject=None,
                    )
                )
            revised.extend(item for item in suffix if item.kind == "sleep")
        elif current.location == home and (
            rain or low_cash or needs.get("urgent_deadline")
        ):
            revised = []
            for item in suffix:
                optional = (
                    item.kind in {"walk", "cafe", "gym"}
                    or item.kind == "travel"
                    and all(
                        endpoint != cfg["locations"]["university"]
                        and endpoint != cfg["locations"]["shop"]
                        for endpoint in (item.origin, item.destination)
                    )
                )
                if optional:
                    item = replace(
                        item,
                        kind="rest",
                        location=home,
                        label=cfg["labels"]["rest"],
                        origin=None,
                        destination=None,
                    )
                revised.append(item)
            if revised == suffix:
                return False
        else:
            return False
        revised = [
            replace(item, id=f"{cause_id}:{index}", revision=current.revision + 1)
            for index, item in enumerate(revised)
        ]
        self.revise(at, revised, cause_id=cause_id)
        return True

    def reserve_task(self, at, task):
        at = require_aware(at)
        current = self.current(at)
        if current.task_id is not None or current.kind in {
            "sleep",
            "wind_down",
            "tea_prepare",
            "tea_break",
            "food_prepare",
            "food_break",
            "short_rest",
            "travel",
            "class",
        }:
            return False
        cfg = self.config["itinerary"]
        minutes = cfg["task_durations"].get(task["kind"], cfg["task_minutes"])
        end = min(current.ends_at, at + timedelta(minutes=minutes))
        if end - at < timedelta(minutes=cfg["task_minimum_minutes"]):
            return False
        cause = "task-plan:" + task["id"] + ":" + to_utc_iso(at)
        reserved = replace(
            current,
            id=cause,
            starts_at=at,
            ends_at=end,
            task_id=task["id"],
            label=cfg["task_labels"].get(task["kind"], cfg["task_label"]),
        )
        following = []
        for index, item in enumerate(self.day(at)):
            if item.ends_at > end:
                following.append(
                    replace(
                        item,
                        id=f"{cause}:after:{index}",
                        starts_at=max(end, item.starts_at),
                    )
                )
        self.revise(at, [reserved, *following], cause_id=cause)
        return True
