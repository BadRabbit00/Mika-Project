"""Choose a break once and persist preparation, rest and its intended return."""

import hashlib
from dataclasses import replace
from datetime import timedelta
from random import Random

from ruamel.yaml import YAML

from src.core.itinerary import Activity
from src.core.life_engine import packed
from src.core.time_utils import require_aware, to_utc_iso


class BreakPlanner:
    def __init__(self, providers):
        self.providers = providers
        self.itinerary, self.database = providers.itinerary, providers.life.database
        self.config = YAML(typ="safe").load(
            providers.config_dir / "activity_transitions.yaml"
        )

    def start(self, at, *, kind, mood, sleep_debt):
        at = require_aware(at)
        with self.database.connection(readonly=True) as c:
            saved = c.execute(
                "SELECT * FROM life_breaks WHERE starts_at=? AND kind=?", (at, kind)
            ).fetchone()
            if saved:
                return dict(saved)
        current = self.itinerary.current(at)
        if not current.can_study:
            return None
        cfg = self.config
        spec = cfg["breaks"][kind]
        identity = (
            "break:"
            + hashlib.sha256((current.id + to_utc_iso(at)).encode()).hexdigest()[:24]
        )
        rng = Random(self.providers.life_config["seed"] + identity)
        tired = (
            mood.A <= cfg["fatigue_energy"] or sleep_debt >= cfg["fatigue_debt_hours"]
        )
        low, high = spec["minutes"]
        if tired:
            low = (low + high) // 2
        duration = rng.randint(low, high)
        prepare = rng.randint(*spec["prepare_minutes"])
        travel = (
            self.providers.life_config["itinerary"]["travel_minutes"]["home_park"]
            if kind == "walk"
            else 0
        )
        available = int((current.ends_at - at).total_seconds() // 60)
        duration = min(
            duration, available - prepare - 2 * travel - cfg["minimum_resume_minutes"]
        )
        if duration < spec["minutes"][0]:
            return None
        if kind == "food" and self.providers.life.state()["pantry"] <= 0:
            return None
        intervals, cursor = [], at

        def add(minutes, name, location="дом", **fields):
            nonlocal cursor
            if not minutes:
                return
            end = cursor + timedelta(minutes=minutes)
            intervals.append(
                Activity(
                    identity + ":" + name,
                    current.day,
                    cursor,
                    end,
                    location,
                    name,
                    cfg["labels"].get(
                        name,
                        self.providers.life_config["itinerary"]["labels"].get(
                            name, name
                        ),
                    ),
                    **fields,
                )
            )
            cursor = end

        if kind == "walk":
            add(travel, "travel", "транспорт", origin="дом", destination="парк")
            intervals[-1] = replace(
                intervals[-1],
                id=identity + ":out",
                label=cfg["labels"]["walking_travel"],
            )
            add(duration, "walk", "парк")
            add(travel, "travel", "транспорт", origin="парк", destination="дом")
            intervals[-1] = replace(
                intervals[-1],
                id=identity + ":back",
                label=cfg["labels"]["walking_travel"],
            )
        else:
            add(prepare, kind + "_prepare")
            add(duration, "short_rest" if kind == "rest" else kind + "_break")
        return_id = identity + ":return"
        intervals.append(replace(current, id=return_id, starts_at=cursor))
        intervals.extend(
            replace(item, id=identity + ":after:" + str(index))
            for index, item in enumerate(self.itinerary.day(at))
            if item.starts_at >= current.ends_at
        )
        reason = "fatigue" if tired else "meal" if kind == "food" else "planned_break"

        def save(c):
            existing = c.execute(
                "SELECT * FROM life_breaks WHERE id=?", (identity,)
            ).fetchone()
            if existing:
                return dict(existing)
            self.itinerary.revise(at, intervals, cause_id=identity, connection=c)
            c.execute(
                "INSERT INTO "
                "life_breaks(id,source_activity_id,return_activity_id,kind,reason,"
                "starts_at,ends_at,payload) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    identity,
                    current.id,
                    return_id,
                    kind,
                    reason,
                    at,
                    cursor,
                    packed(
                        {
                            "minutes": duration,
                            "preparation_minutes": prepare,
                            "sleep_debt": sleep_debt,
                            "energy": mood.A,
                            "return_kind": "study",
                            "return_location": "дом",
                        }
                    ),
                ),
            )
            return dict(
                c.execute(
                    "SELECT * FROM life_breaks WHERE id=?", (identity,)
                ).fetchone()
            )

        return self.database.run_transaction(save)

    def consider(self, at, *, mood, sleep_debt):
        current = self.itinerary.current(at)
        if not current.can_study:
            return None
        if (at - current.starts_at).total_seconds() < 60 * self.config[
            "study_before_break_minutes"
        ]:
            return None
        rng = Random(self.providers.life_config["seed"] + current.id)
        kinds = list(self.config["breaks"])
        weights = [self.config["breaks"][kind]["weight"] for kind in kinds]
        first = rng.choices(kinds, weights=weights, k=1)[0]
        for kind in [first, *(item for item in kinds if item != first)]:
            if kind == "walk" and self.providers.life.needs(at).get("rain"):
                continue
            if result := self.start(at, kind=kind, mood=mood, sleep_debt=sleep_debt):
                return result
        return None

    def stop_if_exhausted(self, at, *, mood, sleep_debt):
        current = self.itinerary.current(at)
        if not current.can_study or (
            mood.A > self.config["stop_energy"]
            and sleep_debt < self.config["stop_debt_hours"]
        ):
            return None
        identity = "exhausted:" + current.id
        intervals = []
        for index, item in enumerate(self.itinerary.day(at)):
            if item.ends_at <= at:
                continue
            values = {"id": identity + str(index), "starts_at": max(at, item.starts_at)}
            if item.can_study:
                values.update(kind="rest", label=self.config["labels"]["short_rest"])
            intervals.append(replace(item, **values))
        self.itinerary.revise(at, intervals, cause_id=identity)
        return "fatigue"

    def ensure_wind_down(self, at):
        """Add preparation to an older saved day only when its bedtime approaches."""
        current = self.itinerary.current(at)
        if (
            current.kind in {"sleep", "wind_down", "travel"}
            or current.location != "дом"
        ):
            return
        remaining = [item for item in self.itinerary.day(at) if item.ends_at > at]
        bedtime = next(
            (item.starts_at for item in remaining if item.kind == "sleep"), None
        )
        if bedtime is None or bedtime - at > timedelta(
            minutes=self.config["wind_down_minutes"]
        ):
            return
        if any(item.kind == "wind_down" for item in remaining):
            return
        identity = "wind-down:" + str(at.date())
        intervals = [
            replace(
                current,
                id=identity,
                starts_at=at,
                ends_at=bedtime,
                kind="wind_down",
                label=self.config["labels"]["wind_down"],
                task_id=None,
            )
        ]
        intervals.extend(
            replace(item, id=identity + ":" + str(index))
            for index, item in enumerate(remaining)
            if item.starts_at >= bedtime
        )
        self.itinerary.revise(at, intervals, cause_id=identity)
