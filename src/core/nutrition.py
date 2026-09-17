"""Elapsed appetite, durable cravings and timed purchase/consumption actions."""

import json
from datetime import timedelta
from random import Random

from src.core.household import Household
from src.core.life_engine import packed
from src.core.time_utils import from_utc_iso, local_clock, require_aware, to_utc_iso


class Nutrition:
    def __init__(self, engine):
        self.engine, self.database = engine, engine.database
        self.config = engine.details["nutrition"]

    def ensure(self, state, at):
        # New installations and upgrades begin at observation time, without
        # inventing missed meals before the hunger model existed.
        state.setdefault(
            "appetite",
            {
                "hunger": self.config["hunger"]["initial"],
                "at": to_utc_iso(at),
                "last_meal_at": None,
            },
        )
        state.setdefault("snacks", [])
        state.setdefault("food_history", {})
        state.setdefault("craving", None)

    def level(self, c, state, at):
        at = require_aware(at)
        anchor = state["appetite"]
        start = from_utc_iso(anchor["at"])
        if at < start:
            raise ValueError("Hunger cannot be evaluated before its saved observation")
        cfg = self.config["hunger"]
        growth = (at - start).total_seconds() / 3600 * cfg["awake_per_hour"]
        for row in c.execute(
            "SELECT starts_at,ends_at,kind FROM life_activities WHERE state='active' "
            "AND starts_at<? AND ends_at>? AND kind IN ('sleep','walk','gym','travel')",
            (at, start),
        ):
            hours = (
                min(at, from_utc_iso(row["ends_at"]))
                - max(start, from_utc_iso(row["starts_at"]))
            ).total_seconds() / 3600
            rate = (
                cfg["asleep_per_hour"] - cfg["awake_per_hour"]
                if row["kind"] == "sleep"
                else cfg["active_extra_per_hour"]
            )
            growth += hours * rate
        return round(max(0, min(100, anchor["hunger"] + growth)), 4)

    def hunger(self, at):
        with self.database.connection(readonly=True) as c:
            return self.level(c, self.engine._state(c), require_aware(at))

    def band(self, value):
        cfg = self.config["hunger"]
        return (
            "very_hungry"
            if value >= cfg["urgent"]
            else "hungry"
            if value >= cfg["irritable"]
            else "peckish"
            if value >= cfg["want_meal"]
            else "sated"
        )

    def view(self, at):
        value, state = self.hunger(at), self.engine.state()
        return {
            "state": self.config["hunger"]["bands"][self.band(value)],
            "last_meal_at": state["appetite"]["last_meal_at"],
            "craving": state["craving"],
            "carried_food": state["snacks"],
        }

    def want(self, product, at, *, cause):
        at = require_aware(at)
        item = self.config["products"][product]

        def save(c):
            state = self.engine._state(c)
            previous = c.execute(
                "SELECT payload FROM life_events WHERE id=?", (cause,)
            ).fetchone()
            if previous:
                return json.loads(previous[0])["changes"]["craving"]
            if state["craving"] and state["craving"]["status"] == "pending":
                return state["craving"]
            craving = {
                "id": cause,
                "product": product,
                "name": item["name"],
                "created_at": to_utc_iso(at),
                "status": "pending",
                "expires_at": to_utc_iso(
                    at
                    + timedelta(hours=self.config["choices"]["craving_lifetime_hours"])
                ),
            }
            state["craving"] = craving
            self.engine._event(
                c,
                state,
                cause,
                at,
                "craving",
                self.config["facts"]["want"].format(**item),
                changes={"craving": craving},
            )
            self.engine._save(c, state, at)
            return craving

        return self.database.run_transaction(save)

    def observe(self, activity, at):
        at = require_aware(at)
        if not activity.can_publish:
            return

        def save(c):
            state = self.engine._state(c)
            self.ensure(state, at)
            level = self.level(c, state, at)
            band = self.band(level)
            previous = state.get("hunger_notice")
            if band in {"hungry", "very_hungry"} and (
                not previous
                or previous["band"] != band
                and at
                >= from_utc_iso(previous["at"])
                + timedelta(hours=self.config["hunger"]["mood_cooldown_hours"])
            ):
                identity = "hunger:" + state["appetite"]["at"] + ":" + band
                self.engine._event(
                    c,
                    state,
                    identity,
                    at,
                    "hunger",
                    self.config["facts"][
                        "very_hungry" if band == "very_hungry" else "hunger"
                    ],
                    activity=activity,
                    mood="life_hunger",
                    changes={"hunger": level},
                )
                state["hunger_notice"] = {"at": to_utc_iso(at), "band": band}
            expired = []
            for lot in state["snacks"]:
                if lot["quantity"] and from_utc_iso(lot["expires_at"]) <= at:
                    expired.append(lot["item"])
                    lot["quantity"] = 0
            if expired:
                self.engine._event(
                    c,
                    state,
                    "snack-expiry:" + to_utc_iso(at),
                    at,
                    "household",
                    self.config["facts"]["expired"],
                    changes={"discarded": expired},
                )
            craving = state["craving"]
            if (
                craving
                and craving["status"] == "pending"
                and from_utc_iso(craving["expires_at"]) <= at
            ):
                craving["status"] = "expired"
            if craving and craving["status"] == "pending":
                product = craving["product"]
                cheapest = min(
                    v["menu"][product]
                    for v in self.config["venues"].values()
                    if product in v["menu"]
                )
                if Household.available(
                    state["snacks"], product, at
                ) == 0 and cheapest > self.budget(state, meal=False, hunger=level):
                    craving.update(status="declined", reason="budget")
                    self.engine._event(
                        c,
                        state,
                        craving["id"] + ":budget",
                        at,
                        "craving",
                        self.config["facts"]["unaffordable"].format(
                            name=craving["name"]
                        ),
                        cause=craving["id"],
                        activity=activity,
                        mood="life_small_disappointment",
                        changes={
                            "cash": state["cash"],
                            "minimum_price": cheapest,
                            "decision": "declined",
                        },
                    )
            state["snacks"] = [lot for lot in state["snacks"] if lot["quantity"]]
            self.engine._save(c, state, at)
            window = at.hour // self.config["choices"]["craving_window_hours"]
            identity = f"craving-check:{at.date()}:{window}"
            if c.execute(
                "SELECT 1 FROM life_state WHERE key=?", (identity,)
            ).fetchone():
                return None
            c.execute("INSERT INTO life_state VALUES (?,?,?)", (identity, "true", at))
            rng = Random(self.engine.config["seed"] + identity)
            if (
                craving and craving["status"] == "pending"
            ) or rng.random() >= self.config["choices"]["craving_chance"]:
                return None
            possible = [
                key
                for key in self.config["choices"]["cravings"]
                if self._repeat_allowed(state, key, at)
            ]
            return (rng.choice(possible), "want:" + identity) if possible else None

        if choice := self.database.run_transaction(save):
            self.want(choice[0], at, cause=choice[1])

    def _repeat_allowed(self, state, product, at):
        last = state["food_history"].get(product)
        return not last or at >= from_utc_iso(last) + timedelta(
            hours=self.config["choices"]["snack_cooldown_hours"]
        )

    def budget(self, state, *, meal, hunger):
        cash = state["cash"]
        reserve = self.config["choices"]["reserve_cash"]
        available = max(0, cash - reserve)
        if meal:
            return cash if hunger >= self.config["hunger"]["urgent"] else available
        return int(available * self.config["choices"]["optional_spend_fraction"])

    def venue_at(self, location):
        return next(
            (
                key
                for key, value in self.config["venues"].items()
                if value["name"] == location
            ),
            self.config["choices"]["legacy_venues"].get(location),
        )

    def open(self, venue, at, *, minutes=0):
        spec = self.config["venues"][venue]
        closing = (
            local_clock(at, "00:00") + timedelta(days=1)
            if spec["closes"] == "24:00"
            else local_clock(at, spec["closes"])
        )
        return (
            local_clock(at, spec["opens"]) <= at
            and at + timedelta(minutes=minutes) <= closing
        )

    def menu_choice(self, venue, at, *, meal, rng):
        state, level = self.engine.state(), self.hunger(at)
        menu = self.config["venues"][venue]["menu"]
        craving = state["craving"]
        options = []
        for key, price in menu.items():
            item = self.config["products"][key]
            if (item["kind"] == "meal") != meal or price > self.budget(
                state, meal=meal, hunger=level
            ):
                continue
            if not meal and not self._repeat_allowed(state, key, at):
                continue
            options.append(key)
        if not meal and craving and craving["status"] == "pending":
            return craving["product"] if craving["product"] in options else None
        if not options:
            return None
        weights = [
            1
            / (1 + abs(min(level, 60) - self.config["products"][key]["satiety"]))
            / menu[key]
            if meal
            else 1 / menu[key]
            for key in options
        ]
        return rng.choices(options, weights=weights, k=1)[0]

    def can_apply(self, c, state, effect, at):
        if effect["action"] == "buy":
            return state["cash"] >= effect["price"] + effect["reserve"]
        if effect["action"] == "home":
            lots = state["prepared_food"]
            untracked = max(0, state["pantry"] - sum(lot["quantity"] for lot in lots))
            available = untracked + sum(
                lot["quantity"]
                for lot in lots
                if lot["owner"] in {"mika", "shared"}
                and from_utc_iso(lot["expires_at"]) > at
            )
            return (
                state["pantry"] >= effect["portions"]
                and available >= effect["portions"]
            )
        return Household.available(state["snacks"], effect["product"], at) >= 1

    def apply(self, c, state, effect, identity, at):
        if not self.can_apply(c, state, effect, at):
            raise ValueError("A food action requires owned stock and enough money")
        if effect["action"] == "buy":
            self.engine._money(
                c, state, identity, at, -effect["price"], "food-purchase"
            )
            state["snacks"].append(
                {
                    "item": effect["product"],
                    "quantity": 1,
                    "owner": "mika",
                    "purchase_id": identity,
                    "expires_at": to_utc_iso(
                        at + timedelta(hours=effect["shelf_hours"])
                    ),
                }
            )
            return
        before = self.level(c, state, at)
        if effect["action"] == "home":
            Household(self.engine).ensure(state, at)
            Household.take(
                state["prepared_food"], "", effect["portions"], at, any_item=True
            )
            state["pantry"] -= effect["portions"]
        else:
            Household.take(state["snacks"], effect["product"], 1, at)
        for key in ("prepared_food", "snacks"):
            state[key] = [lot for lot in state[key] if lot["quantity"]]
        meal = effect.get("meal", False)
        state["appetite"] = {
            "hunger": round(max(0, before - effect["satiety"]), 4),
            "at": to_utc_iso(at),
            "last_meal_at": to_utc_iso(at)
            if meal
            else state["appetite"]["last_meal_at"],
        }
        if state["appetite"]["hunger"] < self.config["hunger"]["irritable"]:
            state.pop("hunger_notice", None)
        state["food_history"][effect["product"]] = to_utc_iso(at)
        craving = state["craving"]
        if (
            craving
            and craving["status"] == "pending"
            and craving["product"] == effect["product"]
        ):
            craving.update(status="satisfied", consumed_at=to_utc_iso(at))
        if meal:
            state["last_meal"] = {
                "at": to_utc_iso(at),
                "product": effect["product"],
                "portions": effect.get("portions", 1),
                "hunger_before": before,
                "hunger_after": state["appetite"]["hunger"],
            }
        if effect["action"] == "home" and 12 <= at.hour < 16:
            chance = self.config["choices"]["home_after_meal_stay_chance"]
            if (
                effect.get("productivity", 0.6)
                < self.engine.details["free_time"]["low_productivity_threshold"]
            ):
                chance += self.config["choices"]["home_after_meal_tired_bonus"]
            if Random(identity + ":stay-home").random() < chance:
                state["stay_home_after_meal"] = str(at.date())

    def _spec(self, activity, nodes, entry, *, label):
        return {
            "entry": entry,
            "label": label,
            "chance": 1,
            "cooldown_days": 0,
            "within_activity": True,
            "essential_food": True,
            "trigger": {"places": [activity.location]},
            "nodes": nodes,
        }

    def order(self, world, activity, at, *, venue, product, cause):
        at = require_aware(at)
        with self.database.connection(readonly=True) as c:
            if c.execute(
                "SELECT 1 FROM world_runs WHERE id=?", ("food:" + cause,)
            ).fetchone():
                return "food:" + cause
        if self.venue_at(activity.location) != venue or not self.open(venue, at):
            return None
        item, state = self.config["products"][product], self.engine.state()
        price = self.config["venues"][venue]["menu"].get(product)
        if price is None or price > self.budget(
            state, meal=item["kind"] == "meal", hunger=self.hunger(at)
        ):
            return None
        duration = self.config["choices"]["queue_minutes"][1] + item["minutes"][1]
        if at + timedelta(minutes=duration) > activity.ends_at or not self.open(
            venue, at, minutes=duration
        ):
            return None
        facts = self.config["facts"]
        reserve = (
            0
            if item["kind"] == "meal"
            and self.hunger(at) >= self.config["hunger"]["urgent"]
            else self.config["choices"]["reserve_cash"]
        )
        nodes = {
            "buy": {
                "minutes": self.config["choices"]["queue_minutes"],
                "label": facts["ordering"],
                "outcomes": [
                    {
                        "id": "paid",
                        "facts": facts["bought"].format(
                            name=item["name"],
                            price=price,
                            venue=self.config["venues"][venue]["name"],
                        ),
                        "food": {
                            "action": "buy",
                            "product": product,
                            "price": price,
                            "reserve": reserve,
                            "shelf_hours": item["shelf_hours"],
                        },
                        "next": "eat",
                    }
                ],
            },
            "eat": {
                "minutes": item["minutes"],
                "label": facts["eating" if item["kind"] == "meal" else "snacking"],
                "outcomes": [
                    {
                        "id": "consumed",
                        "facts": facts["gum"]
                        if product == "gum"
                        else facts["consumed"].format(**item),
                        "mood": "life_meal" if item["kind"] == "meal" else "life_treat",
                        "food": {
                            "action": "eat",
                            "product": product,
                            "meal": item["kind"] == "meal",
                            "satiety": item["satiety"],
                        },
                    }
                ],
            },
        }
        return world.start(
            "food:" + cause,
            activity,
            at,
            identity="food:" + cause,
            spec=self._spec(activity, nodes, "buy", label=facts["eating"]),
        )

    def home_meal(self, world, activity, at):
        cfg, state, facts = (
            self.config["hunger"],
            self.engine.state(),
            self.config["facts"],
        )
        level = self.hunger(at)
        portions = 2 if level >= cfg["large_portion"] and state["pantry"] >= 2 else 1
        effect = {
            "action": "home",
            "product": "home_meal",
            "meal": True,
            "portions": portions,
            "productivity": self.engine.needs(at)["productivity"],
            "satiety": cfg["home_large_satiety"]
            if portions == 2
            else cfg["home_satiety"],
        }
        nodes = {
            "eat": {
                "minutes": cfg["home_large_minutes"]
                if portions == 2
                else cfg["home_minutes"],
                "outcomes": [
                    {
                        "id": "eaten",
                        "facts": facts["home_large" if portions == 2 else "home_meal"],
                        "mood": "life_meal",
                        "food": effect,
                    }
                ],
            }
        }
        slot = (
            activity.id
            if activity.kind == "food_break"
            else activity.day + ":" + activity.kind
        )
        return world.start(
            "home-meal:" + slot,
            activity,
            at,
            identity="home-meal:" + slot,
            spec=self._spec(activity, nodes, "eat", label=facts["home_eating"]),
        )

    def seed(self, world, activity, at):
        if activity.kind == "sleep" or activity.task_id is not None:
            return None
        level = self.hunger(at)
        if activity.kind == "cooking" and activity.location == "дом":
            state = self.engine.state()
            recipes = self.engine.details["food"]["recipes"]
            available = [
                key
                for key in recipes
                if Household(self.engine).ingredients_for(state, key, at)
            ]
            if not available:
                return None
            portions = 2 if level >= self.config["hunger"]["large_portion"] else 1
            recipe = min(
                available,
                key=lambda key: (
                    recipes[key]["portions"] < portions,
                    recipes[key]["minutes"][1],
                ),
            )
            portions = min(portions, recipes[recipe]["portions"])
            facts, cfg = self.config["facts"], self.config["hunger"]
            nodes = {
                "cook": {
                    "minutes": recipes[recipe]["minutes"],
                    "label": facts["cooking_meal"],
                    "outcomes": [
                        {
                            "id": "prepared",
                            "cook": recipe,
                            "facts": facts["cooked"],
                            "next": "eat",
                        }
                    ],
                },
                "eat": {
                    "minutes": cfg["home_large_minutes"]
                    if portions == 2
                    else cfg["home_minutes"],
                    "label": facts["home_eating"],
                    "outcomes": [
                        {
                            "id": "eaten",
                            "facts": facts["home_large"]
                            if portions == 2
                            else facts["home_meal"],
                            "mood": "life_meal",
                            "food": {
                                "action": "home",
                                "product": "home_meal",
                                "meal": True,
                                "portions": portions,
                                "satiety": cfg["home_large_satiety"]
                                if portions == 2
                                else cfg["home_satiety"],
                            },
                        }
                    ],
                },
            }
            return world.start(
                "cook-meal:" + activity.id,
                activity,
                at,
                identity="cook-meal:" + activity.id,
                spec=self._spec(activity, nodes, "cook", label=facts["cooking_meal"]),
            )
        is_meal = activity.kind in {
            "breakfast",
            "lunch",
            "dinner",
            "food_break",
            "early_dinner",
        }
        if is_meal and activity.location == "дом":
            decision = "meal-choice:" + (
                activity.id
                if activity.kind == "food_break"
                else activity.day + ":" + activity.kind
            )

            def decide(c):
                existing = c.execute(
                    "SELECT value FROM life_state WHERE key=?", (decision,)
                ).fetchone()
                if existing:
                    return json.loads(existing[0])
                cfg = self.config["choices"]
                needs = self.engine.needs(at)
                tired = (
                    needs["productivity"] < 0.35
                    or needs["sleep_debt"]
                    >= self.engine.details["morning"]["tired_debt_hours"]
                )
                chance = cfg["skip_meal_chance"].get(activity.kind, 0) + (
                    cfg["tired_skip_bonus"].get(activity.kind, 0) if tired else 0
                )
                skip = level < self.config["hunger"]["want_meal"] or (
                    level < self.config["hunger"]["urgent"]
                    and Random(decision).random() < chance
                )
                c.execute(
                    "INSERT INTO life_state VALUES (?,?,?)",
                    (decision, packed(skip), at),
                )
                if skip:
                    self.engine._event(
                        c,
                        self.engine._state(c),
                        decision,
                        at,
                        "meal_decision",
                        self.config["facts"][
                            "skipped_meal"
                            if level < self.config["hunger"]["want_meal"]
                            else "chose_to_skip"
                        ],
                        activity=activity,
                        changes={"skipped": activity.kind, "hunger": level},
                    )
                return skip

            if self.database.run_transaction(decide):
                return None
            return self.home_meal(world, activity, at)
        if activity.kind in self.config["choices"]["snack_kinds"]:
            state = self.engine.state()
            for lot in sorted(state["snacks"], key=lambda value: value["expires_at"]):
                product = lot["item"]
                item = self.config["products"][product]
                if (
                    not lot["quantity"]
                    or from_utc_iso(lot["expires_at"]) <= at
                    or not self._repeat_allowed(state, product, at)
                    or item["kind"] == "meal"
                    and level < self.config["hunger"]["want_meal"]
                ):
                    continue
                with self.database.connection(readonly=True) as c:
                    if c.execute(
                        "SELECT 1 FROM world_steps s JOIN world_runs r "
                        "ON r.id=s.run_id "
                        "WHERE s.id=? AND r.status='running'",
                        (lot["purchase_id"],),
                    ).fetchone():
                        continue
                facts = self.config["facts"]
                effect = {
                    "action": "eat",
                    "product": product,
                    "satiety": item["satiety"],
                    "meal": item["kind"] == "meal",
                }
                nodes = {
                    "eat": {
                        "minutes": item["minutes"],
                        "outcomes": [
                            {
                                "id": "consumed",
                                "food": effect,
                                "facts": facts["gum"]
                                if product == "gum"
                                else facts["consumed"].format(**item),
                                "mood": "life_meal"
                                if item["kind"] == "meal"
                                else "life_treat",
                            }
                        ],
                    }
                }
                if run := world.start(
                    "carried:" + lot["purchase_id"],
                    activity,
                    at,
                    identity="carried:" + lot["purchase_id"],
                    spec=self._spec(activity, nodes, "eat", label=facts["snacking"]),
                ):
                    return run
        venue = self.venue_at(activity.location)
        if venue and activity.kind in {"dining", "cafe", "shop"}:
            meal = level >= self.config["hunger"]["urgent"] or (
                activity.kind in {"cafe", "dining"}
                and level >= self.config["hunger"]["want_meal"]
            )
            product = self.menu_choice(venue, at, meal=meal, rng=Random(activity.id))
            if product:
                return self.order(
                    world, activity, at, venue=venue, product=product, cause=activity.id
                )
        return None
