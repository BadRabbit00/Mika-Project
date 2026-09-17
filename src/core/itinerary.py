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
            "food_shop",
            "gym",
            "walk",
            "study",
            "side_job",
            "cooking",
            "clinic",
            "hygiene",
            "packing",
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
    def __init__(self, database, schedule, config, *, transitions=None, details=None):
        self.database, self.schedule, self.config = database, schedule, config
        self.transitions = transitions
        self.details = details

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
                    json.dumps(
                        self.config
                        | {
                            "world_details": self.details,
                            "planning_inputs": needs or {},
                        },
                        ensure_ascii=False,
                    ),
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
                    fields.pop("label") if "label" in fields else cfg["labels"][kind],
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

        def university_journey(origin, destination, duration, *, by_taxi=False):
            if not self.details or walking or by_taxi:
                journey(destination, duration, origin)
                if by_taxi:
                    items[-1] = replace(
                        items[-1], label="Поездка на такси в университет"
                    )
                return
            walk = self.details["morning"]["stop_walk_minutes"]
            wait = self.details["morning"]["bus_wait_minutes"]
            bus = duration - 2 * walk - wait
            if bus <= 0:
                raise ValueError("The commute must include both walks and a bus ride")
            departure_stop, arrival_stop = (
                (places["home_stop"], places["university_stop"])
                if origin == home
                else (places["university_stop"], places["home_stop"])
            )
            journey(departure_stop, walk, origin)
            items[-1] = replace(items[-1], label=cfg["labels"]["walk_travel"])
            minutes(wait, departure_stop, "bus_wait")
            journey(arrival_stop, bus, departure_stop)
            items[-1] = replace(items[-1], label=cfg["labels"]["travel"])
            journey(destination, walk, arrival_stop)
            items[-1] = replace(items[-1], label=cfg["labels"]["walk_travel"])

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
        taxi = False
        ate_out = False
        if self.details:
            with self.database.connection(readonly=True) as c:
                decision = c.execute(
                    "SELECT value FROM life_state WHERE key=?",
                    ("morning:" + str(start.date()),),
                ).fetchone()
            taxi = bool(
                decision
                and json.loads(decision[0])["taxi"]
                and needs.get("cash", 0)
                >= self.details["morning"]["taxi_cost"]
                + self.config["money"]["essentials_reserve"]
            )
            if taxi:
                commute = self.details["morning"]["taxi_minutes"]
        if lessons and cursor < lessons[-1].end:
            departure = lessons[0].start - timedelta(
                minutes=commute + cfg["arrival_buffer_minutes"]
            )
            available = max(0, (departure - cursor).total_seconds() / 60)
            minimum = cfg["minimum_preparation_minutes"]
            if self.details:
                morning = self.details["morning"]
                options = dict(morning["optional"])
                mandatory = sum(morning["mandatory"].values())
                for key in morning["skip_order"]:
                    if sum(options.values()) + mandatory <= available:
                        break
                    if rng.random() < morning["skip_chance"]:
                        options.pop(key, None)
                for key in morning["skip_order"]:
                    if sum(options.values()) + mandatory > available:
                        options.pop(key, None)
                for kind, duration in morning["mandatory"].items():
                    minutes(duration, home, kind)
                for kind, duration in options.items():
                    minutes(duration, home, kind if kind != "phone" else "rest")
                until(max(departure, cursor), home, "prepare")
            else:
                breakfast = min(cfg["breakfast_minutes"], max(0, available - minimum))
                minutes(breakfast, home, "breakfast")
                until(
                    max(departure, cursor + timedelta(minutes=minimum)), home, "prepare"
                )
            university_journey(home, university, commute, by_taxi=taxi)
            if taxi:
                commute = self.schedule._data["commute_minutes"]
            for lesson in lessons:
                until(lesson.start, university, "break")
                until(lesson.end, university, "class", subject=lesson.subject)
            food_route = ()
            if self.details and "nutrition" in self.details:
                from src.core.food_plan import after_classes

                breakfast = next(
                    (item for item in items if item.kind == "breakfast"), None
                )
                hunger_cfg = self.details["nutrition"]["hunger"]
                projected = min(
                    100,
                    max(
                        0,
                        (
                            cursor - (breakfast.ends_at if breakfast else sleep.wake)
                        ).total_seconds(),
                    )
                    / 3600
                    * hunger_cfg["awake_per_hour"]
                    + (0 if breakfast else needs.get("hunger", 25)),
                )
                food_route = after_classes(
                    cursor,
                    min(awake_end, local_clock(start, cfg["dinner_start"])),
                    rng,
                    self.config,
                    self.details,
                    needs
                    | {
                        "hunger": projected,
                        "hungry": projected >= hunger_cfg["want_meal"],
                        "cash": needs.get("cash", self.config["money"]["initial_cash"]),
                    },
                )
            if food_route:
                for part in food_route:
                    minutes(
                        part["minutes"],
                        part["location"],
                        part["kind"],
                        label=part["label"],
                        origin=part["origin"],
                        destination=part["destination"],
                    )
                ate_out = True
            else:
                university_journey(university, home, commute)
        else:
            if self.details:
                minutes(
                    self.details["morning"]["mandatory"]["hygiene"], home, "hygiene"
                )
            minutes(cfg["breakfast_minutes"], home, "breakfast")
        minutes(
            cfg["lunch_minutes"], home, "rest" if ate_out else "lunch"
        ) if lessons else until(
            min(local_clock(start, "13:00"), awake_end), home, "rest"
        )
        if not lessons:
            minutes(cfg["lunch_minutes"], home, "lunch")
        outing = None
        cash = needs.get("cash", self.config["money"]["initial_cash"])
        choice = None
        if self.details and not needs.get("urgent_deadline"):
            from src.core.life_dynamics import choose_free_time

            choice = choose_free_time(self.details, needs, rng)
            if ate_out and choice in {"walk", "cafe"}:
                choice = "drawing"
            if choice in {"drawing", "movie", "side_job", "laundry"}:
                length = rng.randint(*self.details["free_time"]["minutes"][choice])
                limit = min(awake_end, local_clock(start, cfg["dinner_start"]))
                if cursor + timedelta(minutes=length) <= limit:
                    minutes(length, home, choice)
            elif choice in {"walk", "cafe", "gym", "shop", "clinic"}:
                outing = choice
        elif not needs.get("ill") and not needs.get("urgent_deadline"):
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
            if self.details and outing in {"walk", "cafe"}:
                from src.core.world_plan import outing_segments

                segments = outing_segments(
                    begins,
                    min(awake_end, local_clock(start, cfg["dinner_start"])),
                    rng,
                    self.config,
                    self.details,
                    cafe_allowed=cash
                    >= self.config["money"]["prices"]["coffee"]
                    + self.config["money"]["essentials_reserve"]
                    and not needs.get("economize"),
                )
                if segments:
                    until(begins, home, "rest")
                    for segment in segments:
                        minutes(
                            segment["minutes"],
                            segment["location"],
                            segment["kind"],
                            label=segment["label"],
                            origin=segment["origin"],
                            destination=segment["destination"],
                        )
                outing = None
            finishes = begins + timedelta(
                minutes=2 * duration + cfg["outing_minutes"].get(outing, 0)
            )
            venue = self.details["venues"].get(places[place]) if self.details else None
            open_for_visit = not venue or (
                begins + timedelta(minutes=duration)
                >= local_clock(start, venue["opens"])
                and finishes - timedelta(minutes=duration)
                <= local_clock(start, venue["closes"])
            )
            if (
                outing
                and open_for_visit
                and finishes < min(awake_end, local_clock(start, cfg["dinner_start"]))
            ):
                until(begins, home, "rest")
                journey(places[place], duration, home)
                minutes(cfg["outing_minutes"][outing], places[place], outing)
                journey(home, duration, places[place])
        study_start = local_clock(
            start,
            self.details["health"]["recovery_study_start"]
            if self.details and needs.get("health_stage") == "recovering"
            else self.schedule._data["day_shape"]["study_window"]["from"],
        )
        until(min(study_start, awake_end), home, "rest")
        budget = None
        if self.details:
            from src.core.life_dynamics import study_minutes

            budget = study_minutes(self.details, needs.get("productivity", 0.6))
            if budget and needs.get("health_stage") == "recovering":
                budget += round(
                    self.details["health"]["recovery_study_bonus_minutes"]
                    * needs["productivity"]
                )
        studying = "rest" if needs.get("ill") and not budget else "study"

        def study_until(end):
            nonlocal budget
            if budget is None:
                until(end, home, studying)
                return
            portion = max(0, min(budget, int((end - cursor).total_seconds() / 60)))
            if portion:
                minutes(portion, home, "study")
                budget -= portion
            until(end, home, "rest")

        study_until(min(local_clock(start, cfg["dinner_start"]), awake_end))
        minutes(cfg["dinner_minutes"], home, "dinner")
        study_until(min(local_clock(start, cfg["evening_rest_start"]), awake_end))
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
        illness = needs.get("ill") and (
            not self.details
            or needs.get("productivity", 0)
            < self.details["productivity"]["minimum_to_study"]
        )
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
                elif self.details and any(
                    v["name"] == current.location
                    for v in self.details.get("nutrition", {})
                    .get("venues", {})
                    .values()
                ):
                    duration = next(
                        v["routes"]["home"]
                        for v in self.details["nutrition"]["venues"].values()
                        if v["name"] == current.location
                    )
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
            rain
            or low_cash
            or needs.get("urgent_deadline")
            or needs.get("stay_home_after_meal")
        ):
            revised = []
            optional = False
            for item in suffix:
                if item.kind == "travel" and item.origin == home:
                    optional = item.destination not in {
                        cfg["locations"]["university"],
                        cfg["locations"]["shop"],
                        cfg["locations"].get("home_stop"),
                        cfg["locations"].get("clinic"),
                    }
                returns_home = item.kind == "travel" and item.destination == home
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
                if returns_home:
                    optional = False
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
