"""Persisted event trees: bounded actions, delayed replies and atomic outcomes."""

import json
from dataclasses import replace
from datetime import timedelta
from random import Random

from ruamel.yaml import YAML

from src.core.household import Household
from src.core.life_dynamics import earned_pay, illness_stage, urgency
from src.core.life_engine import packed
from src.core.time_utils import from_utc_iso, require_aware, to_utc_iso
from src.core.world_people import WorldPeople


class DetailedWorld:
    def __init__(self, engine, *, itinerary=None):
        self.engine, self.database, self.itinerary = engine, engine.database, itinerary
        self.people, self.household = WorldPeople(engine), Household(engine)
        self.catalogue = YAML(typ="safe").load(
            engine.config_dir / "world_scenarios.yaml"
        )["scenarios"]
        self.config = engine.details["scenario"]
        self.validate()

    def validate(self):
        for name, spec in self.catalogue.items():
            if spec["entry"] not in spec["nodes"] or not 0 <= spec["chance"] <= 1:
                raise ValueError(f"Invalid scenario entry or chance: {name}")
            for node in spec["nodes"].values():
                if not 0 < node["minutes"][0] <= node["minutes"][1]:
                    raise ValueError(f"Invalid action duration: {name}")
                for outcome in node["outcomes"]:
                    if (
                        outcome.get("next") is not None
                        and outcome["next"] not in spec["nodes"]
                    ):
                        raise ValueError(f"Unknown scenario branch: {name}")

    @staticmethod
    def _place(spec, activity, at):
        return (
            activity.starts_at <= at < activity.ends_at
            and activity.kind != "sleep"
            and activity.task_id is None
            and (not spec.get("places") or activity.location in spec["places"])
            and (not spec.get("kinds") or activity.kind in spec["kinds"])
            and (
                not spec.get("hours") or spec["hours"][0] <= at.hour < spec["hours"][1]
            )
        )

    def _when(self, c, state, conditions, at, activity, payload):
        for key, expected in conditions.items():
            if key == "cash_gte":
                value = state["cash"] >= expected
            elif key == "pantry_lte":
                value = state["pantry"] <= expected
            elif key == "pantry_gte":
                value = state["pantry"] >= expected
            elif key == "state":
                value = self.engine._matches(state, expected)
            elif key == "flags":
                value = self.engine._matches(state["world_flags"], expected)
            elif key == "device":
                value = (
                    state["devices"][expected["name"]]["condition"] == expected["is"]
                )
            elif key == "shortfall":
                value = (self.engine.shortfall(at, state) > 0) == expected
            elif key == "due":
                last = from_utc_iso(
                    state.get("last_" + expected + "_at", state["started_at"])
                )
                weight = urgency(
                    (at - last).total_seconds() / 86400,
                    self.engine.details["free_time"]["due_days"][expected],
                    self.engine.details["free_time"]["urgency_exponent"],
                )
                value = Random(f"{expected}:{at.date()}").random() < weight
            elif key == "recent_activity":
                value = bool(
                    c.execute(
                        "SELECT 1 FROM life_events e LEFT JOIN life_activities a "
                        "ON a.id=e.activity_id WHERE (e.kind=? OR a.kind=?) AND e.at>? "
                        "AND e.at<=?",
                        (
                            expected["kind"],
                            expected["kind"],
                            at - timedelta(hours=expected["hours"]),
                            at,
                        ),
                    ).fetchone()
                )
            elif key == "returned_home":
                value = (
                    bool(
                        c.execute(
                            "SELECT 1 FROM life_activities WHERE state='active' "
                            "AND kind='travel' AND destination='дом' AND starts_at>=? "
                            "AND ends_at<=?",
                            (payload["opened_at"], at),
                        ).fetchone()
                    )
                    == expected
                )
            elif key == "productivity_lte":
                value = payload.get("productivity", 0.5) <= expected
            elif key in {"npc_busy", "npc_free"}:
                free = self.people.available(c, expected, at)
                value = free if key == "npc_free" else not free
            elif key == "npc_mood":
                value = (
                    self.engine._person(state, expected["person"], at)["mood"]
                    == expected["is"]
                )
            elif key == "recipe":
                value = self.household.ingredients_for(state, expected, at) is not None
            elif key == "appointment_present":
                began = (
                    at
                    if payload.get("waiting")
                    else from_utc_iso(payload["started_at"])
                )
                value = bool(
                    c.execute(
                        "SELECT 1 FROM world_appointments WHERE person=? "
                        "AND location=? AND id=? "
                        "AND starts_at<=? AND ends_at>=? AND ends_at>? "
                        "AND status='reserved'",
                        (
                            expected,
                            activity.location,
                            payload.get("appointment_id"),
                            began,
                            at,
                            began,
                        ),
                    ).fetchone()
                )
            elif key == "ill":
                value = (illness_stage(state, at) != "well") == expected
            elif key == "rain":
                value = (
                    bool(
                        state.get("weather", {}).get("rain")
                        and at < from_utc_iso(state["weather"]["valid_until"])
                    )
                    == expected
                )
            elif key == "coffee_broken":
                value = (state["coffee_stage"] in {"broken", "leaking"}) == expected
            elif key == "cat_home":
                value = state["cat_home"] == expected
            elif key == "gym_visited":
                value = (state["gym_visits"] > 0) == expected
            else:
                raise ValueError(f"Unknown scenario condition: {key}")
            if not value:
                return False
        return True

    def start(
        self, scenario, activity, at, *, identity=None, productivity=0.5, spec=None
    ):
        at = require_aware(at)
        spec = self.catalogue[scenario] if spec is None else spec
        if not self._place(spec["trigger"], activity, at):
            return None
        identity = identity or f"scenario:{scenario}:{at.date()}"
        rng = Random(identity + ":" + spec["entry"])
        duration = rng.randint(*spec["nodes"][spec["entry"]]["minutes"])
        if (
            at + timedelta(minutes=max(duration, spec["trigger"].get("min_minutes", 0)))
            > activity.ends_at
        ):
            return None

        def save(c):
            old = c.execute(
                "SELECT id FROM world_runs WHERE id=?", (identity,)
            ).fetchone()
            if old:
                return old[0]
            if c.execute(
                "SELECT 1 FROM world_runs WHERE scenario=? AND "
                "(status='running' OR created_at>?)",
                (
                    scenario,
                    at - timedelta(days=spec["cooldown_days"]),
                ),
            ).fetchone():
                return None
            payload = {
                "spec": spec,
                "step": 0,
                "started_at": to_utc_iso(at),
                "opened_at": to_utc_iso(at),
                "activity_id": activity.id,
                "parent_end": to_utc_iso(activity.ends_at),
                "productivity": round(productivity, 4),
                "last_event": None,
            }
            state = self.engine._state(c)
            if (
                not spec.get("essential_food")
                and illness_stage(state, at) == "acute"
                and scenario
                not in {
                    "ill_friend_visit",
                    "recovery_notes",
                    "ill_food_delivery",
                }
            ):
                return None
            if not self._choices(
                c, state, spec["nodes"][spec["entry"]], at, activity, payload
            ):
                return None
            if self.occupied(c, at):
                return None
            c.execute(
                "INSERT INTO world_runs(id,scenario,node,created_at,due_at,payload) "
                "VALUES (?,?,?,?,?,?)",
                (
                    identity,
                    scenario,
                    spec["entry"],
                    at,
                    at + timedelta(minutes=duration),
                    packed(payload),
                ),
            )
            return identity

        return self.database.run_transaction(save)

    @staticmethod
    def active_action(c, activity_id, at):
        for row in c.execute(
            "SELECT id,node,due_at,payload FROM world_runs WHERE status='running' "
            "AND due_at>? ORDER BY due_at,id",
            (at,),
        ):
            payload = json.loads(row["payload"])
            node = payload["spec"]["nodes"][row["node"]]
            if (
                not node.get("passive")
                and not payload.get("waiting")
                and payload["activity_id"] == activity_id
                and from_utc_iso(payload["started_at"]) <= at
            ):
                return {
                    "id": row["id"] + ":" + str(payload["step"]),
                    "label": node.get("label", payload["spec"]["label"]),
                    "until": from_utc_iso(row["due_at"]),
                }
        return None

    @staticmethod
    def occupied(c, at, *, exclude=None):
        for row in c.execute(
            "SELECT id,node,due_at,payload FROM world_runs WHERE status='running'"
        ):
            if row["id"] == exclude:
                continue
            payload = json.loads(row["payload"])
            if (
                not payload["spec"]["nodes"][row["node"]].get("passive")
                and not payload.get("waiting")
                and from_utc_iso(payload["started_at"])
                <= at
                < from_utc_iso(row["due_at"])
            ):
                return True
        return False

    def _meeting(self, c, request, at):
        if self.itinerary is None:
            return None
        duration = timedelta(minutes=request["minutes"])
        buffer = timedelta(
            minutes=self.engine.details["calendar"]["travel_buffer_minutes"]
        )
        for item in self.itinerary.day(at):
            starts = max(item.starts_at, at + buffer)
            if (
                item.location == request["location"]
                and item.kind in {"rest", "walk", "park_rest", "cafe"}
                and item.task_id is None
                and starts + duration <= item.ends_at
                and self.people.available(
                    c, request["person"], starts - buffer, starts + duration + buffer
                )
            ):
                return starts, starts + duration, item
        if request["location"] == "дом":
            return None
        cfg = self.engine.config["itinerary"]
        place = next(
            (
                key
                for key, value in cfg["locations"].items()
                if value == request["location"]
            ),
            None,
        )
        if not place or "home_" + place not in cfg["travel_minutes"]:
            return None
        travel = timedelta(minutes=cfg["travel_minutes"]["home_" + place])
        for item in self.itinerary.day(at):
            departure = max(item.starts_at, at + buffer)
            starts, ends = departure + travel, departure + travel + duration
            venue = self.engine.details["venues"][request["location"]]
            from src.core.time_utils import local_clock

            if (
                item.location != "дом"
                or item.kind != "rest"
                or item.task_id
                or ends + travel > item.ends_at
                or starts < local_clock(starts, venue["opens"])
                or ends > local_clock(ends, venue["closes"])
                or not self.people.available(
                    c, request["person"], departure, ends + travel
                )
            ):
                continue
            base = f"meeting:{request['person']}:{to_utc_iso(departure)}"
            outbound = replace(
                item,
                id=base + ":out",
                starts_at=departure,
                ends_at=starts,
                kind="travel",
                location=cfg["locations"]["road"],
                origin="дом",
                destination=request["location"],
                label=cfg["labels"]["walk_travel"],
            )
            meeting = replace(
                item,
                id=base + ":visit",
                starts_at=starts,
                ends_at=ends,
                kind="park_rest" if place == "park" else "cafe",
                location=request["location"],
                label=cfg["labels"]["park_rest" if place == "park" else "cafe"],
            )
            back = replace(
                outbound,
                id=base + ":back",
                starts_at=ends,
                ends_at=ends + travel,
                origin=request["location"],
                destination="дом",
            )
            return starts, ends, meeting, (outbound, meeting, back)
        return None

    def _choices(self, c, state, node, at, activity, payload):
        choices = []
        for choice in node["outcomes"]:
            if not self._when(c, state, choice.get("when", {}), at, activity, payload):
                continue
            if choice.get("appointment") and not self._meeting(
                c, choice["appointment"], at
            ):
                continue
            if (
                choice.get("cook")
                and self.household.ingredients_for(state, choice["cook"], at) is None
            ):
                continue
            if state["cash"] + choice.get("money", 0) < 0:
                continue
            if effect := choice.get("food"):
                from src.core.nutrition import Nutrition

                if not Nutrition(self.engine).can_apply(c, state, effect, at):
                    continue
            choices.append(choice)
            # A specific precondition takes precedence over a fallback outcome.
            if choice.get("when") or choice.get("appointment"):
                break
        return choices

    def _effects(self, c, state, choice, identity, at, payload, activity):
        if effect := choice.get("food"):
            from src.core.nutrition import Nutrition

            Nutrition(self.engine).apply(c, state, effect, identity, at)
        if person := choice.get("contact"):
            self.engine._contact_person(c, state, person, at)["last_contact"] = (
                to_utc_iso(at)
            )
        self.engine._effects(state, choice.get("effects", {}), at)
        state["world_flags"].update(choice.get("flags", {}))
        if choice.get("flags", {}).get("laundry") == "clean":
            state["last_laundry_at"] = to_utc_iso(at)
        if delta := choice.get("money"):
            self.engine._money(c, state, identity, at, delta, "scenario")
        if amount := choice.get("save_money"):
            self.engine._money(c, state, identity, at, -amount, "saving-debit")
            self.engine._money(
                c, state, identity, at, amount, "saving-credit", "savings"
            )
        if recipe := choice.get("cook"):
            self.household.cook_in(state, recipe, at)
        if device := choice.get("device"):
            state["devices"][device["name"]]["condition"] = device["condition"]
            if device.get("reset_uses"):
                state["devices"][device["name"]]["uses"] = 0
        if name := choice.get("device_use"):
            item = state["devices"][name]
            item["uses"] += 1
            if item["uses"] >= item["lifespan_uses"]:
                item["condition"] = "worn"
        if choice.get("earned_pay"):
            payload["pay"] = earned_pay(
                self.engine.details, payload["productivity"], identity
            )
            state.setdefault("receivables", {})[identity] = payload["pay"] | {
                "due_at": to_utc_iso(
                    at
                    + timedelta(
                        hours=self.engine.details["free_time"][
                            "side_job_payment_delay_hours"
                        ]
                    )
                )
            }
            payload["pay_id"] = identity
        if choice.get("pay_earned"):
            owed = state.setdefault("receivables", {}).pop(payload["pay_id"], None)
            if owed is None:
                raise ValueError("Payment requires a completed unpaid work receipt")
            self.engine._money(c, state, identity, at, owed["actual"], "work-paid")
        if days := choice.get("extend_deadline_days"):
            state["coursework_due"] = to_utc_iso(
                from_utc_iso(state["coursework_due"]) + timedelta(days=days)
            )
            c.execute(
                "UPDATE life_tasks SET deadline=? WHERE "
                "kind IN ('coursework','submit_coursework') AND status='pending'",
                (state["coursework_due"],),
            )
        if request := choice.get("appointment"):
            reservation = self._meeting(c, request, at)
            starts, ends, item = reservation[:3]
            if len(reservation) == 4:
                route = reservation[3]
                following = [
                    replace(
                        activity,
                        id=identity + ":after:" + str(index),
                        starts_at=max(route[-1].ends_at, activity.starts_at),
                    )
                    for index, activity in enumerate(self.itinerary.day(at))
                    if activity.ends_at > route[-1].ends_at
                ]
                self.itinerary.revise(
                    route[0].starts_at,
                    [*route, *following],
                    cause_id=identity,
                    connection=c,
                )
            c.execute(
                "INSERT INTO world_appointments VALUES (?,?,?,?,?,'reserved',?)",
                (
                    identity,
                    request["person"],
                    starts,
                    ends,
                    request["location"],
                    packed({"activity_id": item.id, "cause": identity}),
                ),
            )
            payload["appointment_id"] = identity
        if choice.get("attend"):
            c.execute(
                "UPDATE world_appointments SET status='attended' "
                "WHERE id=? AND status='reserved'",
                (payload.get("appointment_id"),),
            )

    def _step(self, row, activity, at):
        def save(c, activity=activity, at=at):
            current = c.execute(
                "SELECT * FROM world_runs WHERE id=?", (row["id"],)
            ).fetchone()
            if current["status"] != "running" or from_utc_iso(current["due_at"]) > at:
                return
            payload = json.loads(current["payload"])
            spec = payload["spec"]
            node = spec["nodes"][current["node"]]
            started = from_utc_iso(payload["started_at"])
            identity = current["id"] + f":{payload['step']}"
            physical = not node.get("passive")
            if physical and not payload.get("waiting"):
                previous = c.execute(
                    "SELECT * FROM life_activities WHERE id=? AND state='active'",
                    (payload["activity_id"],),
                ).fetchone()
                due = from_utc_iso(current["due_at"])
                if previous and from_utc_iso(
                    previous["starts_at"]
                ) <= started < due <= from_utc_iso(previous["ends_at"]):
                    from src.core.itinerary import Activity

                    activity, at = Activity.from_row(previous), due
            permitted = self._place(
                node if node.get("places") or node.get("kinds") else spec["trigger"],
                activity,
                min(at, activity.ends_at - timedelta(microseconds=1)),
            )
            moved = payload["activity_id"] != activity.id
            if any(
                outcome.get("food") for outcome in node["outcomes"]
            ) and not self._choices(
                c, self.engine._state(c), node, at, activity, payload
            ):
                self._cancel(c, current, at, "food_or_budget_unavailable")
                return
            if physical and (not permitted or moved or payload.get("waiting")):
                if spec["within_activity"] and not permitted:
                    self._cancel(c, current, at, "parent_activity_ended")
                    return
                if (
                    permitted
                    and not self.occupied(c, at, exclude=row["id"])
                    and self._choices(
                        c, self.engine._state(c), node, at, activity, payload
                    )
                ):
                    duration = Random(identity).randint(*node["minutes"])
                    limit = activity.ends_at
                    if any(o.get("attend") for o in node["outcomes"]):
                        appointment = c.execute(
                            "SELECT ends_at FROM world_appointments WHERE id=?",
                            (payload.get("appointment_id"),),
                        ).fetchone()
                        if appointment:
                            limit = min(limit, from_utc_iso(appointment[0]))
                    if at + timedelta(minutes=duration) <= limit:
                        payload.update(
                            started_at=to_utc_iso(at),
                            activity_id=activity.id,
                            parent_end=to_utc_iso(activity.ends_at),
                            waiting=False,
                        )
                        c.execute(
                            "UPDATE world_runs SET due_at=?,payload=? WHERE id=?",
                            (
                                at + timedelta(minutes=duration),
                                packed(payload),
                                row["id"],
                            ),
                        )
                return
            if physical and at > from_utc_iso(payload["parent_end"]):
                return
            if physical and at > activity.ends_at:
                return
            state = self.engine._state(c)
            choices = self._choices(c, state, node, at, activity, payload)
            if not choices:
                return
            choice = Random(identity).choices(
                choices, [item.get("weight", 1) for item in choices], k=1
            )[0]
            before = json.loads(packed(state))
            self.engine._event(
                c,
                state,
                identity,
                at,
                "situation",
                choice["facts"],
                cause=payload["last_event"],
                activity=activity,
                mood=choice.get("mood"),
                outcome=choice["id"],
            )
            self._effects(c, state, choice, identity, at, payload, activity)
            changes = {
                k: {"before": before.get(k), "after": v}
                for k, v in state.items()
                if before.get(k) != v
            }
            evidence = {
                "scenario": current["scenario"],
                "node": current["node"],
                "outcome": choice["id"],
                "changes": changes,
                "parent_activity_id": payload["activity_id"],
                "pay": payload.get("pay"),
            }
            c.execute(
                "INSERT INTO world_steps VALUES (?,?,?,?,?,?,?)",
                (
                    identity,
                    current["id"],
                    identity,
                    current["node"],
                    started,
                    from_utc_iso(current["due_at"]),
                    packed(evidence),
                ),
            )
            c.execute(
                "UPDATE life_events SET payload=json_set(payload,'$.world',json(?)) "
                "WHERE id=?",
                (packed(evidence), identity),
            )
            next_node = choice.get("next")
            payload.update(
                step=payload["step"] + 1,
                last_event=identity,
                started_at=to_utc_iso(at),
                activity_id=activity.id,
                parent_end=to_utc_iso(activity.ends_at),
                waiting=bool(next_node and not spec["nodes"][next_node].get("passive")),
            )
            duration = (
                Random(identity + ":next").randint(*spec["nodes"][next_node]["minutes"])
                if next_node
                else 1
            )
            c.execute(
                "UPDATE world_runs SET node=?,status=?,due_at=?,payload=? WHERE id=?",
                (
                    next_node or current["node"],
                    "running" if next_node else "completed",
                    at
                    + timedelta(
                        minutes=Random(identity + ":wait").randint(
                            *spec["nodes"][next_node].get("wait_minutes", [0, 0])
                        )
                    )
                    if payload.get("waiting")
                    else at + timedelta(minutes=duration),
                    packed(payload),
                    row["id"],
                ),
            )
            self.engine._save(c, state, at)

        self.database.run_transaction(save)

    def _cancel(self, c, row, at, reason):
        payload = json.loads(row["payload"])
        payload.update(cancel_reason=reason, cancelled_at=to_utc_iso(at))
        c.execute(
            "UPDATE world_runs SET status='cancelled',payload=? WHERE id=?",
            (packed(payload), row["id"]),
        )
        self.engine._event(
            c,
            self.engine._state(c),
            row["id"] + ":cancelled",
            at,
            "scenario_cancelled",
            self.engine.details["facts"]["ended"],
            cause=payload.get("last_event"),
            silent=True,
            changes={
                "scenario": row["scenario"],
                "node": row["node"],
                "reason": reason,
            },
        )

    def advance(self, activity, at, *, seed=True, productivity=0.5):
        at = require_aware(at)
        if activity.kind == "sleep":
            return
        self.household.expire(at)
        self.people.advance(at)
        with self.database.connection(readonly=True) as c:
            rows = c.execute(
                "SELECT * FROM world_runs WHERE status='running' ORDER BY due_at,id"
            ).fetchall()
        for row in rows:
            if not json.loads(row["payload"])["spec"].get(
                "persistent"
            ) and at - from_utc_iso(row["created_at"]) > timedelta(
                days=self.config["max_deferred_days"]
            ):
                self.database.run_transaction(
                    lambda c, row=row: self._cancel(c, row, at, "deferral_expired")
                )
                continue
            self._step(row, activity, at)
            with self.database.connection(readonly=True) as c:
                following = c.execute(
                    "SELECT * FROM world_runs WHERE id=?", (row["id"],)
                ).fetchone()
            after = json.loads(following["payload"])
            if (
                following["status"] == "running"
                and after.get("waiting")
                and after["step"] > json.loads(row["payload"])["step"]
            ):
                # Reserve an immediately due follow-up before unrelated tasks
                # can take its interval. Positive durations prevent instant work.
                self._step(following, activity, at)
        if not seed:
            return
        self.seed(activity, at, productivity=productivity)

    def seed(self, activity, at, *, productivity=0.5):
        at = require_aware(at)
        if activity.kind == "sleep":
            return
        with self.database.connection(readonly=True) as c:
            count = c.execute(
                "SELECT count(*) FROM world_runs WHERE status='running'"
            ).fetchone()[0]
            existing = {r[0] for r in c.execute("SELECT id FROM world_runs")}
        candidates = list(self.catalogue)
        Random(f"scenarios:{at.date()}:{activity.id}").shuffle(candidates)
        for name in candidates:
            if count >= self.config["max_parallel"]:
                break
            identity = f"scenario:{name}:{at.date()}"
            if identity in existing:
                continue
            if Random(identity).random() >= self.catalogue[name]["chance"]:
                continue
            if self.start(
                name, activity, at, identity=identity, productivity=productivity
            ):
                count += 1

    def health(self, at):
        at = require_aware(at)

        def save(c):
            state = self.engine._state(c)
            if state.get("ill_until") and not state.get("ill_started"):
                previous = c.execute(
                    "SELECT at FROM life_events WHERE kind='illness' "
                    "AND at<=? ORDER BY at DESC LIMIT 1",
                    (at,),
                ).fetchone()
                state["ill_started"] = (
                    previous[0]
                    if previous
                    else to_utc_iso(
                        from_utc_iso(state["ill_until"])
                        - timedelta(
                            hours=self.engine.rules["seeds"]["illness"]["effects"][
                                "ill_until"
                            ]["after_hours"]
                        )
                    )
                )
            stage = illness_stage(state, at)
            if state["world_flags"].get("symptoms_stage") == stage:
                return
            state["world_flags"]["symptoms_stage"] = stage
            self.engine._event(
                c,
                state,
                f"health:{state.get('ill_started')}:{stage}",
                at,
                "health",
                self.engine.details["health"]["stages"][stage]["facts"],
                mood="life_illness" if stage == "acute" else "life_recovery",
            )
            self.engine._save(c, state, at)

        self.database.run_transaction(save)

    def complete_appointments(self, at):
        at = require_aware(at)

        def save(c):
            state = self.engine._state(c)
            for row in c.execute(
                "SELECT id,ends_at FROM life_activities WHERE kind='clinic' "
                "AND state='active' AND ends_at<=?",
                (at,),
            ).fetchall():
                identity = "clinic-finished:" + row["id"]
                if c.execute(
                    "SELECT 1 FROM life_events WHERE id=?", (identity,)
                ).fetchone():
                    continue
                self.engine._event(
                    c,
                    state,
                    identity,
                    from_utc_iso(row["ends_at"]),
                    "health",
                    self.engine.details["facts"]["clinic_finished"],
                    mood="life_relief",
                    changes={"ill_certificate": True},
                )
                state["ill_certificate"] = True
                state["last_clinic_at"] = to_utc_iso(at)
            c.execute(
                "UPDATE world_appointments SET status='missed' "
                "WHERE ends_at<=? AND status='reserved'",
                (at,),
            )
            for row in c.execute(
                "SELECT r.* FROM world_runs r JOIN world_appointments a ON "
                "a.id=json_extract(r.payload,'$.appointment_id') "
                "WHERE r.status='running' AND a.status='missed'"
            ).fetchall():
                self._cancel(c, row, at, "appointment_missed")
            self.engine._save(c, state, at)

        self.database.run_transaction(save)
