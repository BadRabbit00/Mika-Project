"""Ingredient lots, expiry and meals with transactional purchase/cooking receipts."""

from datetime import timedelta

from src.core.time_utils import from_utc_iso, require_aware, to_utc_iso


class Household:
    def __init__(self, engine):
        self.engine, self.database = engine, engine.database
        self.config = engine.details["food"]

    def ensure(self, state, at):
        state.setdefault("ingredients", [])
        state.setdefault("prepared_food", [])
        state.setdefault(
            "devices",
            {key: dict(value) for key, value in self.engine.details["devices"].items()},
        )
        state.setdefault(
            "world_flags", dict(self.engine.details["scenario"]["variables"])
        )
        # Adopt existing prepared portions without inventing a purchase.
        known = sum(item["quantity"] for item in state["prepared_food"])
        difference = state["pantry"] - known
        if difference > 0:
            state["prepared_food"].append(
                {
                    "item": "existing_portion",
                    "quantity": difference,
                    "owner": "mika",
                    "expires_at": to_utc_iso(at + timedelta(days=2)),
                }
            )
        elif difference < 0:
            self.take(
                state["prepared_food"],
                "existing_portion",
                -difference,
                at,
                any_item=True,
                allow_expired=True,
            )

    @staticmethod
    def available(lots, item, at):
        return sum(
            lot["quantity"]
            for lot in lots
            if lot["item"] == item
            and lot["owner"] in {"mika", "shared"}
            and from_utc_iso(lot["expires_at"]) > at
        )

    @staticmethod
    def take(lots, item, quantity, at, *, any_item=False, allow_expired=False):
        for lot in sorted(lots, key=lambda row: row["expires_at"]):
            if (not any_item and lot["item"] != item) or lot["owner"] not in {
                "mika",
                "shared",
            }:
                continue
            if not allow_expired and from_utc_iso(lot["expires_at"]) <= at:
                continue
            used = min(quantity, lot["quantity"])
            lot["quantity"] -= used
            quantity -= used
        if quantity:
            raise ValueError("An inventory withdrawal cannot exceed owned stock")

    def ingredients_for(self, state, recipe, at):
        result = {}
        for item, amount in self.config["recipes"][recipe]["ingredients"].items():
            if self.available(state["ingredients"], item, at) >= amount:
                result[item] = result.get(item, 0) + amount
            else:
                substitute = (
                    self.config["recipes"][recipe].get("substitutes", {}).get(item)
                )
                if not substitute:
                    return None
                for alternative, count in substitute.items():
                    result[alternative] = result.get(alternative, 0) + count
        return (
            result
            if all(
                self.available(state["ingredients"], k, at) >= v
                for k, v in result.items()
            )
            else None
        )

    def add_basket(self, state, at):
        for item, quantity in self.config["basket"].items():
            state["ingredients"].append(
                {
                    "item": item,
                    "quantity": quantity,
                    "owner": "shared",
                    "expires_at": to_utc_iso(
                        at
                        + timedelta(days=self.config["ingredients"][item]["shelf_days"])
                    ),
                }
            )

    def shop(self, at, *, cause):
        at = require_aware(at)
        identity = "ingredients:" + cause

        def save(c):
            if c.execute(
                "SELECT 1 FROM life_events WHERE id=?", (identity,)
            ).fetchone():
                return identity
            state = self.engine._state(c)
            cost = self.config["basket_cost"]
            if state["cash"] < cost:
                return None
            self.engine._event(
                c,
                state,
                identity,
                at,
                "groceries",
                self.engine.details["facts"]["groceries"],
                cause=cause
                if c.execute(
                    "SELECT 1 FROM life_events WHERE id=?", (cause,)
                ).fetchone()
                else None,
                changes={"ingredients_added": self.config["basket"], "money": -cost},
            )
            self.engine._money(c, state, identity, at, -cost, "ingredients")
            self.add_basket(state, at)
            self.engine._save(c, state, at)
            return identity

        return self.database.run_transaction(save)

    def cook_in(self, state, recipe, at):
        ingredients = self.ingredients_for(state, recipe, at)
        if ingredients is None:
            return None
        for item, quantity in ingredients.items():
            self.take(state["ingredients"], item, quantity, at)
        portions = self.config["recipes"][recipe]["portions"]
        state["prepared_food"].append(
            {
                "item": recipe,
                "quantity": portions,
                "owner": "shared",
                "expires_at": to_utc_iso(
                    at + timedelta(days=self.config["prepared_shelf_days"])
                ),
            }
        )
        state["pantry"] += portions
        return ingredients

    def cook(self, recipe, at, *, cause):
        at = require_aware(at)
        identity = "recipe:" + cause

        def save(c):
            if c.execute(
                "SELECT 1 FROM life_events WHERE id=?", (identity,)
            ).fetchone():
                return identity
            state = self.engine._state(c)
            ingredients = self.cook_in(state, recipe, at)
            if ingredients is None:
                return None
            self.engine._event(
                c,
                state,
                identity,
                at,
                "cooking",
                self.engine.details["facts"]["cooked"],
                cause=cause
                if c.execute(
                    "SELECT 1 FROM life_events WHERE id=?", (cause,)
                ).fetchone()
                else None,
                mood="life_achievement",
                changes={
                    "recipe": recipe,
                    "ingredients_used": ingredients,
                    "portions": self.config["recipes"][recipe]["portions"],
                },
            )
            self.engine._save(c, state, at)
            return identity

        return self.database.run_transaction(save)

    def expire(self, at):
        at = require_aware(at)

        def save(c):
            state = self.engine._state(c)
            self.ensure(state, at)
            expired = {}
            for key in ("ingredients", "prepared_food"):
                for lot in state[key]:
                    if lot["quantity"] and from_utc_iso(lot["expires_at"]) <= at:
                        expired[lot["item"]] = (
                            expired.get(lot["item"], 0) + lot["quantity"]
                        )
                        if key == "prepared_food":
                            state["pantry"] -= lot["quantity"]
                        lot["quantity"] = 0
                state[key] = [lot for lot in state[key] if lot["quantity"]]
            if expired:
                self.engine._event(
                    c,
                    state,
                    "food-expiry:" + to_utc_iso(at),
                    at,
                    "household",
                    self.engine.details["facts"]["spoiled"],
                    mood="life_frustration",
                    changes={"discarded": expired},
                )
            self.engine._save(c, state, at)
            return expired

        return self.database.run_transaction(save)
