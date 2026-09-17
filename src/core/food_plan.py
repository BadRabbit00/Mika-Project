"""Food-aware routes and future-only revisions of saved daily activities."""

from dataclasses import replace
from datetime import timedelta
from random import Random

from src.core.time_utils import from_utc_iso, local_clock, require_aware


def venue_open(venue, at, duration):
    closing = (
        local_clock(at, "00:00") + timedelta(days=1)
        if venue["closes"] == "24:00"
        else local_clock(at, venue["closes"])
    )
    return (
        local_clock(at, venue["opens"]) <= at
        and at + timedelta(minutes=duration) <= closing
    )


def after_classes(at, limit, rng, config, details, needs):
    """Save one affordable meal route or fall back to the already planned home meal."""
    at, limit = require_aware(at), require_aware(limit)
    cfg = details["nutrition"]
    if not needs.get("hungry") or needs.get("ill"):
        return ()
    budget = max(
        0,
        needs["cash"]
        - cfg["choices"]["reserve_cash"]
        - 2 * config["money"]["prices"]["transport"],
    )
    if not budget or rng.random() >= cfg["choices"]["eat_out_chance"]:
        return ()
    visit = cfg["choices"][
        "meal_visit_large_minutes"
        if needs["hunger"] >= cfg["hunger"]["large_portion"]
        else "meal_visit_minutes"
    ]
    candidates = []
    for venue in cfg["venues"].values():
        prices = [
            price
            for key, price in venue["menu"].items()
            if cfg["products"][key]["kind"] == "meal" and price <= budget
        ]
        arrives = at + timedelta(minutes=venue["routes"]["university"])
        finishes = arrives + timedelta(minutes=visit + venue["routes"]["home"])
        if prices and finishes <= limit and venue_open(venue, arrives, visit):
            candidates.append((venue, min(prices)))
    if not candidates:
        return ()
    venue = rng.choices(
        [v for v, _ in candidates], weights=[1 / price for _, price in candidates], k=1
    )[0]
    facts, road = cfg["facts"], config["itinerary"]["locations"]["road"]
    result = []

    def add(minutes, location, kind, label, origin=None, destination=None):
        result.append(
            dict(
                minutes=minutes,
                location=location,
                kind=kind,
                label=label,
                origin=origin,
                destination=destination,
            )
        )

    postpone = (
        needs["hunger"] < cfg["hunger"]["urgent"]
        and not needs.get("urgent_deadline")
        and not needs.get("rain")
        and rng.random() < cfg["choices"]["postpone_lunch_chance"]
    )
    park_walk = rng.randint(*cfg["choices"]["walk_before_meal_minutes"])
    if (
        postpone
        and at
        + timedelta(
            minutes=20
            + park_walk
            + venue["routes"]["park"]
            + visit
            + venue["routes"]["home"]
        )
        <= limit
    ):
        add(
            20,
            road,
            "travel",
            config["itinerary"]["labels"]["walk_travel"],
            "универ",
            "парк",
        )
        add(park_walk, "парк", "walk", facts["walk_before_meal"])
        add(
            venue["routes"]["park"],
            road,
            "travel",
            facts["food_travel"],
            "парк",
            venue["name"],
        )
    else:
        add(
            venue["routes"]["university"],
            road,
            "travel",
            facts["food_travel"],
            "универ",
            venue["name"],
        )
    add(visit, venue["name"], "dining", facts["meal_visit"])
    add(
        venue["routes"]["home"],
        road,
        "travel",
        facts["homecoming"],
        venue["name"],
        "дом",
    )
    return tuple(result)


def prioritize_meal_route(engine, itinerary, activity, at):
    """Actual hunger can remove a future park detour without rerolling lunch."""
    from src.core.detailed_world import DetailedWorld
    from src.core.nutrition import Nutrition

    at = require_aware(at)
    cfg = engine.details["nutrition"]
    if (
        activity.location != "универ"
        or activity.kind not in {"class", "break"}
        or activity.task_id is not None
        or Nutrition(engine).hunger(at) < cfg["hunger"]["urgent"]
    ):
        return False
    with engine.database.connection(readonly=True) as c:
        if DetailedWorld.occupied(c, at):
            return False
    day = itinerary.day(at)
    class_end = max((item.ends_at for item in day if item.kind == "class"), default=at)
    for index, departure in enumerate(day):
        route = day[index : index + 5]
        if (
            departure.starts_at < class_end
            or departure.starts_at <= at
            or departure.origin != "универ"
            or departure.destination != "парк"
            or [item.kind for item in route]
            != ["travel", "walk", "travel", "dining", "travel"]
            or any(item.task_id is not None for item in route)
        ):
            continue
        meal, returning = route[3:]
        venue = next(
            (v for v in cfg["venues"].values() if v["name"] == meal.location), None
        )
        if venue is None or returning.destination != "дом":
            continue
        arrives = departure.starts_at + timedelta(minutes=venue["routes"]["university"])
        meal_duration = meal.ends_at - meal.starts_at
        eaten = arrives + meal_duration
        home = eaten + timedelta(minutes=venue["routes"]["home"])
        if home >= returning.ends_at or not venue_open(
            venue, arrives, meal_duration.total_seconds() / 60
        ):
            continue
        identity = "meal-priority:" + departure.id
        replacements = [
            replace(item, starts_at=max(at, item.starts_at))
            for item in day[:index]
            if item.ends_at > at
        ]
        replacements.extend(
            [
                replace(
                    departure,
                    ends_at=arrives,
                    destination=meal.location,
                    label=cfg["facts"]["food_travel"],
                ),
                replace(meal, starts_at=arrives, ends_at=eaten),
                replace(returning, starts_at=eaten, ends_at=home),
                replace(
                    meal,
                    starts_at=home,
                    ends_at=returning.ends_at,
                    kind="rest",
                    location="дом",
                    label=engine.config["itinerary"]["labels"]["rest"],
                ),
                *day[index + 5 :],
            ]
        )
        replacements = [
            replace(item, id=f"{identity}:{number}", revision=item.revision + 1)
            for number, item in enumerate(replacements)
        ]

        def save(c, replacements=replacements, identity=identity, meal=meal):
            itinerary.revise(at, replacements, cause_id=identity, connection=c)
            engine._event(
                c,
                engine._state(c),
                identity,
                at,
                "meal_decision",
                cfg["facts"]["meal_first"],
                activity=replacements[0],
                changes={
                    "cancelled_detour": "парк",
                    "meal_venue": meal.location,
                },
            )

        engine.database.run_transaction(save)
        return True
    return False


def revise_for_food(engine, itinerary, activity, at):
    """A craving can cause a real shop trip; hunger can bring dinner forward."""
    from src.core.detailed_world import DetailedWorld
    from src.core.nutrition import Nutrition

    at = require_aware(at)
    if (
        activity.location != "дом"
        or activity.task_id is not None
        or activity.kind
        not in {
            "rest",
            "study",
            "movie",
            "drawing",
        }
    ):
        return False
    with engine.database.connection(readonly=True) as c:
        if DetailedWorld.occupied(c, at):
            return False
    food, state = Nutrition(engine), engine.state()
    cfg, level = food.config, food.hunger(at)
    identity, parts = None, []
    meal_limit = activity.ends_at
    for upcoming in itinerary.day(at):
        if upcoming.starts_at < activity.ends_at:
            continue
        if (
            upcoming.location != "дом"
            or upcoming.task_id is not None
            or upcoming.kind
            not in {"rest", "study", "movie", "drawing", "breakfast", "lunch", "dinner"}
        ):
            break
        meal_limit = upcoming.ends_at
    last_meal = state["appetite"]["last_meal_at"]
    gap = last_meal is None or at >= from_utc_iso(last_meal) + timedelta(
        hours=cfg["hunger"]["minimum_meal_gap_hours"]
    )
    if state["pantry"] == 0 and level >= cfg["hunger"]["want_meal"]:
        from src.core.household import Household

        household = Household(engine)
        recipes = engine.details["food"]["recipes"]
        possible = [key for key in recipes if household.ingredients_for(state, key, at)]
        shopping = not possible
        if shopping:
            if (
                state["cash"] < engine.details["food"]["basket_cost"]
                or engine.needs(at)["ill"]
            ):
                return False
            possible = list(recipes)
        cook = (
            max(recipes[key]["minutes"][1] for key in possible)
            + cfg["hunger"]["home_large_minutes"][1]
            + 2
        )
        if shopping:
            route = engine.config["itinerary"]["travel_minutes"]["home_shop"]
            visit = engine.config["itinerary"]["task_durations"]["groceries"] + 5
            venue = cfg["venues"][cfg["choices"]["legacy_venues"]["магазин"]]
            if not venue_open(venue, at + timedelta(minutes=route), visit):
                return False
            road = engine.config["itinerary"]["locations"]["road"]
            parts = [
                dict(
                    minutes=route,
                    location=road,
                    kind="travel",
                    origin="дом",
                    destination="магазин",
                    label=cfg["facts"]["food_travel"],
                ),
                dict(
                    minutes=visit,
                    location="магазин",
                    kind="food_shop",
                    label=cfg["facts"]["food_shopping"],
                ),
                dict(
                    minutes=route,
                    location=road,
                    kind="travel",
                    origin="магазин",
                    destination="дом",
                    label=cfg["facts"]["homecoming"],
                ),
            ]
        parts.append(
            dict(
                minutes=cook,
                location="дом",
                kind="cooking",
                label=cfg["facts"]["cooking_meal"],
            )
        )
        if at + timedelta(minutes=sum(part["minutes"] for part in parts)) > meal_limit:
            return False
        identity = "hunger-food:" + activity.id
    elif (
        at.hour >= cfg["hunger"]["early_dinner_from"]
        and level >= cfg["hunger"]["early_dinner_hunger"]
        and state["pantry"] > 0
        and gap
    ):
        minutes = cfg["hunger"]["home_large_minutes"][1] + 2
        if at + timedelta(minutes=minutes) > meal_limit:
            return False
        identity = "early-dinner:" + str(at.date())
        parts = [
            dict(
                minutes=minutes,
                location="дом",
                kind="early_dinner",
                label=cfg["facts"]["early_dinner"],
            )
        ]
    else:
        craving = state["craving"]
        if (
            not craving
            or craving["status"] != "pending"
            or activity.kind not in cfg["choices"]["home_trip_kinds"]
            or level >= cfg["hunger"]["urgent"]
        ):
            return False
        product = craving["product"]
        if any(
            lot["item"] == product
            and lot["quantity"]
            and from_utc_iso(lot["expires_at"]) > at
            for lot in state["snacks"]
        ):
            return False
        possible = []
        for key, venue in cfg["venues"].items():
            visit = max(
                cfg["choices"]["store_visit_minutes"],
                cfg["choices"]["queue_minutes"][1]
                + cfg["products"][product]["minutes"][1]
                + 2,
            )
            duration = 2 * venue["routes"]["home"] + visit
            if (
                venue["category"] == "shop"
                and product in venue["menu"]
                and venue["menu"][product]
                <= food.budget(state, meal=False, hunger=level)
                and at + timedelta(minutes=duration) <= activity.ends_at
                and food.open(
                    key, at + timedelta(minutes=venue["routes"]["home"]), minutes=visit
                )
            ):
                possible.append((key, venue, visit))
        if not possible:
            return False
        key, venue, visit = Random(craving["id"]).choice(possible)
        identity = "craving-trip:" + craving["id"]
        route = venue["routes"]["home"]
        road = engine.config["itinerary"]["locations"]["road"]
        parts = [
            dict(
                minutes=route,
                location=road,
                kind="travel",
                origin="дом",
                destination=venue["name"],
                label=cfg["facts"]["craving_trip"],
            ),
            dict(
                minutes=visit,
                location=venue["name"],
                kind="shop",
                label=cfg["facts"]["snacking"],
            ),
            dict(
                minutes=route,
                location=road,
                kind="travel",
                origin=venue["name"],
                destination="дом",
                label=cfg["facts"]["homecoming"],
            ),
        ]
    with engine.database.connection(readonly=True) as c:
        if c.execute(
            "SELECT 1 FROM life_state WHERE key=?", ("itinerary.revision:" + identity,)
        ).fetchone():
            return False
    cursor, replacements = at, []
    for index, part in enumerate(parts):
        end = cursor + timedelta(minutes=part.pop("minutes"))
        replacements.append(
            replace(
                activity,
                id=identity + ":" + str(index),
                starts_at=cursor,
                ends_at=end,
                **({"origin": None, "destination": None, "subject": None} | part),
            )
        )
        cursor = end
    for index, item in enumerate(itinerary.day(at)):
        if item.ends_at > cursor:
            replacements.append(
                replace(
                    item,
                    id=identity + ":after:" + str(index),
                    starts_at=max(cursor, item.starts_at),
                )
            )
    itinerary.revise(at, replacements, cause_id=identity)
    return True
