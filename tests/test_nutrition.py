"""Hunger, saved food choices and purchases are independent of generated prose."""

from datetime import datetime, timedelta
from pathlib import Path
from random import Random

import pytest
from ruamel.yaml import YAML

from src.core.db import Database
from src.core.itinerary import Activity, Itinerary, _continuous
from src.core.life_engine import LifeEngine
from src.core.time_utils import ALMATY, from_utc_iso

AT = datetime(2026, 9, 17, 13, tzinfo=ALMATY)


@pytest.fixture
def engine(tmp_path):
    db = Database(tmp_path / "food.sqlite3")
    db.initialize()
    config = YAML(typ="safe").load(Path("config/life_simulation.yaml"))
    engine = LifeEngine(db, config, Path("config"))
    engine.bootstrap(AT)
    return engine


def activity(at=AT, *, kind="rest", location="дом", minutes=90):
    return Activity(
        "food-fixture",
        str(at.date()),
        at,
        at + timedelta(minutes=minutes),
        location,
        kind,
        "Fixture activity",
    )


def test_hunger_depends_on_elapsed_time_not_empty_cupboard(engine):
    from src.core.nutrition import Nutrition

    food = Nutrition(engine)
    before = food.hunger(AT)
    later = AT + timedelta(hours=5)
    assert engine.state()["pantry"] > 0
    assert food.hunger(later) > before
    assert engine.needs(later)["hungry"]
    assert food.hunger(later) == Nutrition(engine).hunger(later)
    with pytest.raises(ValueError, match="aware"):
        food.hunger(AT.replace(tzinfo=None))


def test_sleep_accumulates_hunger_more_slowly(engine):
    from src.core.nutrition import Nutrition

    food = Nutrition(engine)
    awake = food.hunger(AT + timedelta(hours=5))
    sleep = activity(kind="sleep", minutes=300)

    def save(c):
        c.execute(
            "INSERT INTO life_days VALUES (?,?,?,?)", (sleep.day, "test", "{}", AT)
        )
        Itinerary._insert(c, sleep)

    engine.database.run_transaction(save)
    assert food.hunger(AT) < food.hunger(sleep.ends_at) < awake


def test_food_catalogue_has_distinct_prices_and_non_meal_treats(engine):
    cfg = engine.details["nutrition"]
    venues = list(cfg["venues"].values())
    assert (
        sum(v["category"] in {"cafe", "coffee_shop", "restaurant"} for v in venues) == 6
    )
    assert sum(v["category"] == "fast_food" for v in venues) == 2
    assert sum(v["category"] == "shop" for v in venues) == 5
    assert len({v["name"] for v in venues}) == len(venues)
    assert cfg["products"]["gum"]["satiety"] == 0
    assert cfg["products"]["coffee"]["satiety"] == 0
    assert (
        cfg["products"]["chocolate"]["satiety"]
        < cfg["products"]["rice_bowl"]["satiety"]
    )
    assert all(price > 0 for venue in venues for price in venue["menu"].values())


def finish_step(world, engine, visit, run):
    with engine.database.connection(readonly=True) as c:
        due = from_utc_iso(
            c.execute("SELECT due_at FROM world_runs WHERE id=?", (run,)).fetchone()[0]
        )
    world.advance(visit, due, seed=False)
    return due


def test_purchase_and_eating_are_separate_idempotent_actions(engine):
    from src.core.detailed_world import DetailedWorld
    from src.core.nutrition import Nutrition

    food, world = Nutrition(engine), DetailedWorld(engine)
    visit = activity(
        location=engine.details["nutrition"]["venues"]["campus_cafe"]["name"],
        kind="dining",
    )
    cash = engine.state()["cash"]
    run = food.order(
        world, visit, AT, venue="campus_cafe", product="rice_bowl", cause="lunch"
    )
    assert run
    assert engine.state()["cash"] == cash
    purchased_at = finish_step(world, engine, visit, run)
    after_purchase = engine.state()
    assert after_purchase["cash"] < cash
    assert food.hunger(purchased_at) >= food.hunger(AT)
    hunger_before_meal = food.hunger(purchased_at)
    assert sum(lot["quantity"] for lot in after_purchase["snacks"]) == 1
    world.advance(visit, purchased_at, seed=False)
    eaten_at = finish_step(world, engine, visit, run)
    final = engine.state()
    assert food.hunger(eaten_at) < hunger_before_meal
    assert sum(lot["quantity"] for lot in final["snacks"]) == 0
    world.advance(visit, eaten_at + timedelta(minutes=1), seed=False)
    assert engine.state() == final
    assert (
        food.order(
            world, visit, AT, venue="campus_cafe", product="rice_bowl", cause="lunch"
        )
        == run
    )
    assert engine.state()["cash"] == after_purchase["cash"]


def test_no_purchase_without_money_and_no_food_from_gum(engine):
    from src.core.detailed_world import DetailedWorld
    from src.core.nutrition import Nutrition

    def empty(c):
        state = engine._state(c)
        state["cash"] = 0
        engine._save(c, state, AT)

    engine.database.run_transaction(empty)
    food, world = Nutrition(engine), DetailedWorld(engine)
    place = engine.details["nutrition"]["venues"]["corner_shop"]["name"]
    visit = activity(location=place, kind="shop")
    assert (
        food.order(world, visit, AT, venue="corner_shop", product="gum", cause="want")
        is None
    )
    assert engine.state()["cash"] == 0


def test_craving_survives_restart_without_reroll_or_duplicate_notice(engine):
    from src.core.nutrition import Nutrition

    food = Nutrition(engine)
    first = food.want("chocolate", AT, cause="saved-craving")
    assert first["status"] == "pending"
    assert not engine.needs(AT)["hungry"]
    assert Nutrition(engine).want("chips", AT, cause="saved-craving") == first
    with engine.database.connection(readonly=True) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM life_events WHERE id=?", ("saved-craving",)
            ).fetchone()[0]
            == 1
        )


def test_after_class_routes_respect_budget_travel_and_meal_duration(engine):
    from src.core.food_plan import after_classes

    needs = {
        "hungry": True,
        "hunger": 80,
        "cash": 22000,
        "economize": False,
        "productivity": 0.7,
    }
    limit = AT.replace(hour=18)
    routes = [
        after_classes(AT, limit, Random(seed), engine.config, engine.details, needs)
        for seed in range(15)
    ]
    route = next(route for route in routes if route)
    assert route[0]["origin"] == "универ"
    assert route[-1]["destination"] == "дом"
    assert sum(item["minutes"] for item in route) >= 60
    assert any(item["kind"] == "dining" for item in route)
    assert (
        after_classes(
            AT, limit, Random(1), engine.config, engine.details, needs | {"cash": 0}
        )
        == ()
    )
    cursor, leaves = AT, []
    for i, part in enumerate(route):
        end = cursor + timedelta(minutes=part["minutes"])
        leaves.append(
            Activity(
                str(i),
                str(AT.date()),
                cursor,
                end,
                part["location"],
                part["kind"],
                part["label"],
                origin=part["origin"],
                destination=part["destination"],
            )
        )
        cursor = end
    _continuous(leaves)
    assert cursor <= limit


def test_food_order_rejects_wrong_place_closed_venue_and_sleep(engine):
    from src.core.detailed_world import DetailedWorld
    from src.core.nutrition import Nutrition

    food, world = Nutrition(engine), DetailedWorld(engine)
    assert (
        food.order(
            world,
            activity(),
            AT,
            venue="campus_cafe",
            product="rice_bowl",
            cause="remote",
        )
        is None
    )
    sleeping = activity(
        kind="sleep",
        location=engine.details["nutrition"]["venues"]["campus_cafe"]["name"],
    )
    assert (
        food.order(
            world, sleeping, AT, venue="campus_cafe", product="rice_bowl", cause="sleep"
        )
        is None
    )
    night = AT.replace(hour=3)
    closed = activity(night, kind="dining", location=sleeping.location)
    assert (
        food.order(
            world,
            closed,
            night,
            venue="campus_cafe",
            product="rice_bowl",
            cause="closed",
        )
        is None
    )


def test_irritability_threshold_is_a_saved_mood_effect_not_a_polling_penalty(engine):
    from src.core.nutrition import Nutrition

    food = Nutrition(engine)
    later = AT + timedelta(hours=3)
    visit = activity(later)
    assert food.hunger(later) >= 40
    food.observe(visit, later)
    with engine.database.connection(readonly=True) as c:
        effects = c.execute(
            "SELECT id FROM life_effects WHERE id LIKE 'hunger:%'"
        ).fetchall()
    assert len(effects) == 1
    food.observe(visit, later + timedelta(minutes=1))
    with engine.database.connection(readonly=True) as c:
        assert (
            len(
                c.execute(
                    "SELECT id FROM life_effects WHERE id LIKE 'hunger:%'"
                ).fetchall()
            )
            == 1
        )


def test_skipped_lunch_brings_dinner_forward_with_a_larger_saved_portion(engine):
    from src.core.detailed_world import DetailedWorld
    from src.core.food_plan import revise_for_food
    from src.core.nutrition import Nutrition

    def setup(c):
        state = engine._state(c)
        state["appetite"]["hunger"] = 70
        engine._save(c, state, AT)
        c.execute(
            "INSERT INTO life_days VALUES (?,?,?,?)", (str(AT.date()), "test", "{}", AT)
        )
        Itinerary._insert(c, activity(AT, kind="study", minutes=500))

    engine.database.run_transaction(setup)
    itinerary = Itinerary(
        engine.database, engine.schedule, engine.config, details=engine.details
    )
    early = AT.replace(hour=17)
    assert revise_for_food(engine, itinerary, itinerary.current(early), early)
    dinner = itinerary.current(early)
    assert dinner.kind == "early_dinner" and not dinner.can_study
    world, food = DetailedWorld(engine), Nutrition(engine)
    before = engine.state()["pantry"]
    run = food.seed(world, dinner, early)
    assert run
    eaten = finish_step(world, engine, dinner, run)
    assert engine.state()["pantry"] == before - 2
    assert engine.state()["last_meal"]["portions"] == 2
    assert food.hunger(eaten) < 40
    assert not revise_for_food(engine, itinerary, itinerary.current(eaten), eaten)


def test_optional_breakfast_can_be_removed_without_removing_hygiene_or_packing(engine):
    from types import SimpleNamespace

    from src.core.time_utils import local_clock

    wednesday = AT.replace(day=16, hour=0)
    lesson = engine.schedule.classes(wednesday)[0]
    wake = lesson.start - timedelta(
        minutes=engine.schedule._data["commute_minutes"] + 20
    )
    sleep = SimpleNamespace(bedtime=wednesday, wake=wake)
    following = SimpleNamespace(bedtime=local_clock(wednesday, "23:30"))
    itinerary = Itinerary(
        engine.database, engine.schedule, engine.config, details=engine.details
    )
    plan = itinerary.build(
        wednesday,
        sleep,
        following,
        needs={"cash": 20000, "productivity": 0.3, "sleep_debt": 9, "hunger": 45},
    )
    assert not any(item.kind == "breakfast" for item in plan)
    assert {"hygiene", "packing"} <= {item.kind for item in plan}
    _continuous(plan)


def test_moderate_hunger_can_lose_to_a_walk_but_urgent_hunger_cannot(engine):
    from src.core.food_plan import after_classes

    needs = {"hungry": True, "hunger": 45, "cash": 22000}
    routes = [
        after_classes(
            AT, AT.replace(hour=19), Random(seed), engine.config, engine.details, needs
        )
        for seed in range(50)
    ]
    assert any(
        route
        and next(part for part in route if part["kind"] != "travel")["kind"] == "walk"
        for route in routes
    )
    for seed in range(20):
        route = after_classes(
            AT,
            AT.replace(hour=19),
            Random(seed),
            engine.config,
            engine.details,
            needs | {"hunger": 90},
        )
        assert (
            not route
            or next(part for part in route if part["kind"] != "travel")["kind"]
            == "dining"
        )


def test_gum_improves_mood_without_replacing_a_meal(engine):
    from src.core.detailed_world import DetailedWorld
    from src.core.nutrition import Nutrition

    food, world = Nutrition(engine), DetailedWorld(engine)
    visit = activity(location=food.config["venues"]["corner_shop"]["name"], kind="shop")
    run = food.order(world, visit, AT, venue="corner_shop", product="gum", cause="gum")
    bought = finish_step(world, engine, visit, run)
    before = food.hunger(bought)
    world.advance(visit, bought, seed=False)
    eaten = finish_step(world, engine, visit, run)
    assert food.hunger(eaten) > before
    assert engine.state()["appetite"]["last_meal_at"] is None
    with engine.database.connection(readonly=True) as c:
        assert c.execute(
            "SELECT 1 FROM life_effects WHERE "
            "json_extract(payload,'$.event')='life_treat'"
        ).fetchone()


def test_carried_food_is_not_lost_when_purchase_and_consumption_cross_locations(engine):
    from dataclasses import replace

    from src.core.detailed_world import DetailedWorld
    from src.core.nutrition import Nutrition

    food, world = Nutrition(engine), DetailedWorld(engine)
    food.want("chocolate", AT, cause="want-chocolate")
    visit = activity(location=food.config["venues"]["corner_shop"]["name"], kind="shop")
    run = food.order(
        world, visit, AT, venue="corner_shop", product="chocolate", cause="chocolate"
    )
    bought = finish_step(world, engine, visit, run)
    cash = engine.state()["cash"]
    home_at = bought + timedelta(minutes=10)
    home = replace(activity(home_at), id="back-home")
    world.advance(home, home_at, seed=False)
    replacement = food.seed(world, home, home_at)
    assert replacement and replacement.startswith("carried:")
    finish_step(world, engine, home, replacement)
    assert engine.state()["craving"]["status"] == "satisfied"
    assert engine.state()["cash"] == cash
    assert sum(lot["quantity"] for lot in engine.state()["snacks"]) == 0


def test_craving_can_be_declined_for_money_without_a_fake_purchase(engine):
    from src.core.nutrition import Nutrition

    def broke(c):
        state = engine._state(c)
        state["cash"] = 10
        engine._save(c, state, AT)

    engine.database.run_transaction(broke)
    food = Nutrition(engine)
    food.want("chips", AT, cause="chips-too-expensive")
    food.observe(activity(), AT)
    assert engine.state()["craving"]["status"] == "declined"
    assert engine.state()["cash"] == 10
    with engine.database.connection(readonly=True) as c:
        assert c.execute(
            "SELECT 1 FROM life_events WHERE id='chips-too-expensive:budget'"
        ).fetchone()
        assert not c.execute(
            "SELECT 1 FROM money_ledger WHERE id LIKE '%:food-purchase'"
        ).fetchone()


def test_expired_home_portions_cannot_be_consumed_before_the_expiry_sweep(engine):
    from src.core.detailed_world import DetailedWorld
    from src.core.nutrition import Nutrition

    later = AT + timedelta(days=3)
    visit = activity(later, kind="lunch")
    assert engine.state()["pantry"] > 0
    food = Nutrition(engine)
    assert food.home_meal(DetailedWorld(engine), visit, later) is None
    assert engine.state()["appetite"]["last_meal_at"] is None


def test_recorded_food_actions_pass_existing_claim_checks(engine):
    from src.core.activity_claims import activity_conflicts

    cfg = engine.details["nutrition"]
    rules = Path("config/activity_transitions.yaml")
    facts = cfg["facts"]["bought"].format(
        name="шоколадка", price=450, venue=cfg["venues"]["corner_shop"]["name"]
    )
    assert not activity_conflicts("Я купила шоколадку.", {"facts": facts}, rules)
    assert "unplanned_purchase" in activity_conflicts(
        "Я купила шоколадку.",
        {"facts": cfg["facts"]["want"].format(name="шоколадка")},
        rules,
    )
    assert not activity_conflicts(
        "Я иду в магазин.",
        {
            "current": {
                "kind": "travel",
                "destination": cfg["venues"]["corner_shop"]["name"],
            }
        },
        rules,
    )
    assert not activity_conflicts(
        "Сейчас ужинаю дома.", {"current": {"kind": "early_dinner"}}, rules
    )


def test_restaurant_arrival_proves_location_without_claiming_a_purchase(engine):
    from src.core.activity_claims import activity_conflicts

    venue = engine.details["nutrition"]["venues"]["samal"]["name"]
    event = engine.activity(activity(location=venue, kind="dining"), AT)
    evidence = {"current": {"kind": "dining"}, "facts": event["facts"]}
    rules = Path("config/activity_transitions.yaml")
    assert not activity_conflicts("Я в ресторане.", evidence, rules)
    assert "unplanned_purchase" in activity_conflicts(
        "Я уже оплатила заказ.", evidence, rules
    )


async def test_regular_alarm_leaves_room_for_breakfast(tmp_path):
    from src.providers import RuntimeProviders

    at = AT.replace(day=15, hour=23)
    db = Database(tmp_path / "morning.sqlite3")
    db.initialize()
    providers = RuntimeProviders(db, Path("config"), clock=lambda: at)
    try:
        tomorrow = at + timedelta(days=1)
        sleep = providers.sleep.plan(tomorrow.date(), at=at)
        following = providers.sleep.plan((tomorrow + timedelta(days=1)).date(), at=at)
        plan = providers.itinerary.build(
            tomorrow.replace(hour=0),
            sleep,
            following,
            needs={"cash": 22000, "productivity": 0.6},
        )
        assert {"breakfast", "hygiene", "packing"} <= {item.kind for item in plan}
    finally:
        await providers.close()


def test_hungry_empty_home_creates_groceries_cooking_and_meal_route(engine):
    from src.core.food_plan import revise_for_food

    def setup(c):
        state = engine._state(c)
        state["pantry"] = 0
        state["prepared_food"] = []
        state["appetite"]["hunger"] = 60
        engine._save(c, state, AT)
        c.execute(
            "INSERT INTO life_days VALUES (?,?,?,?)", (str(AT.date()), "test", "{}", AT)
        )
        Itinerary._insert(c, activity(AT, minutes=240))

    engine.database.run_transaction(setup)
    itinerary = Itinerary(
        engine.database, engine.schedule, engine.config, details=engine.details
    )
    assert revise_for_food(engine, itinerary, itinerary.current(AT), AT)
    saved = itinerary.day(AT)
    kinds = [item.kind for item in saved]
    assert kinds[:4] == ["travel", "food_shop", "travel", "cooking"]
    assert saved[1].location == "магазин"
    assert saved[3].location == "дом"
    _continuous(saved)
    assert not revise_for_food(engine, itinerary, itinerary.current(AT), AT)
    assert itinerary.day(AT) == saved


def test_groceries_to_cooking_to_eating_uses_stock_and_time_once(engine):
    from src.core.detailed_world import DetailedWorld
    from src.core.food_plan import revise_for_food
    from src.core.nutrition import Nutrition

    def setup(c):
        state = engine._state(c)
        state.update(pantry=0, prepared_food=[])
        state["appetite"]["hunger"] = 60
        engine._save(c, state, AT)
        c.execute(
            "INSERT INTO life_days VALUES (?,?,?,?)", (str(AT.date()), "test", "{}", AT)
        )
        Itinerary._insert(c, activity(AT, minutes=240))

    engine.database.run_transaction(setup)
    itinerary = Itinerary(
        engine.database, engine.schedule, engine.config, details=engine.details
    )
    assert revise_for_food(engine, itinerary, itinerary.current(AT), AT)
    plan = itinerary.day(AT)
    shop, cooking = plan[1], plan[3]
    engine.notice(AT)
    task = next(task for task in engine.tasks() if task["kind"] == "groceries")
    cash = engine.state()["cash"]
    assert engine.execute(task["id"], at=shop.ends_at, activity=shop)
    assert engine.state()["pantry"] == 0
    assert engine.state()["cash"] == cash - 4400
    world, food = DetailedWorld(engine), Nutrition(engine)
    run = food.seed(world, cooking, cooking.starts_at)
    assert run
    ready = finish_step(world, engine, cooking, run)
    before = food.hunger(ready)
    assert engine.state()["pantry"] > 0
    world.advance(cooking, ready, seed=False)
    eaten = finish_step(world, engine, cooking, run)
    assert eaten <= cooking.ends_at
    assert food.hunger(eaten) < before
    assert engine.state()["last_meal"]["portions"] >= 1
    final = engine.state()
    engine.execute(task["id"], at=shop.ends_at, activity=shop)
    world.advance(cooking, eaten, seed=False)
    assert engine.state() == final


def test_real_meal_can_replace_flexible_home_blocks_but_not_fixed_sleep(engine):
    from dataclasses import replace

    from src.core.food_plan import revise_for_food

    def setup(c):
        state = engine._state(c)
        state.update(pantry=0, prepared_food=[])
        state["appetite"]["hunger"] = 70
        engine._save(c, state, AT)
        first = activity(AT, minutes=30)
        second = replace(
            activity(first.ends_at, kind="study", minutes=120), id="study-after-rest"
        )
        third = replace(
            activity(second.ends_at, kind="sleep", minutes=60), id="fixed-sleep"
        )
        c.execute(
            "INSERT INTO life_days VALUES (?,?,?,?)", (str(AT.date()), "test", "{}", AT)
        )
        for item in (first, second, third):
            Itinerary._insert(c, item)

    engine.database.run_transaction(setup)
    itinerary = Itinerary(
        engine.database, engine.schedule, engine.config, details=engine.details
    )
    bedtime = itinerary.day(AT)[-1].starts_at
    assert revise_for_food(engine, itinerary, itinerary.current(AT), AT)
    assert itinerary.day(AT)[-1].starts_at == bedtime
    assert any(item.kind == "cooking" for item in itinerary.day(AT))
    _continuous(itinerary.day(AT))


def test_cooking_reserves_its_eating_followup_before_unrelated_tasks(engine):
    from src.core.detailed_world import DetailedWorld
    from src.core.household import Household
    from src.core.nutrition import Nutrition

    Household(engine).shop(AT, cause="cook-ingredients")
    visit = activity(AT, kind="cooking", minutes=90)
    world = DetailedWorld(engine)
    run = Nutrition(engine).seed(world, visit, AT)
    ready = finish_step(world, engine, visit, run)
    with engine.database.connection(readonly=True) as c:
        assert world.occupied(c, ready)
        action = world.active_action(c, visit.id, ready)
        assert action is not None
        assert action["until"] > ready
        assert c.execute("SELECT count(*) FROM world_steps").fetchone()[0] == 1
    assert engine.state()["appetite"]["last_meal_at"] is None
    eaten = finish_step(world, engine, visit, run)
    assert engine.state()["appetite"]["last_meal_at"] is not None
    final = engine.state()
    world.advance(visit, eaten, seed=False)
    assert engine.state() == final


@pytest.mark.parametrize(("level", "changed"), [(45, False), (90, True)])
def test_actual_hunger_revises_the_saved_walk_before_lunch(engine, level, changed):
    from dataclasses import replace

    from src.core.food_plan import prioritize_meal_route

    venue = engine.details["nutrition"]["venues"]["burger_corner"]
    plan, cursor = [], AT
    for index, (kind, place, minutes, origin, destination) in enumerate(
        [
            ("class", "универ", 60, None, None),
            ("travel", "транспорт", 20, "универ", "парк"),
            ("walk", "парк", 40, None, None),
            ("travel", "транспорт", 10, "парк", venue["name"]),
            ("dining", venue["name"], 40, None, None),
            ("travel", "транспорт", 18, venue["name"], "дом"),
            ("rest", "дом", 120, None, None),
        ]
    ):
        item = replace(
            activity(cursor, location=place, minutes=minutes),
            id=f"saved-meal-route:{index}",
            kind=kind,
            origin=origin,
            destination=destination,
        )
        plan.append(item)
        cursor = item.ends_at

    def save(c):
        state = engine._state(c)
        state["appetite"]["hunger"] = level
        engine._save(c, state, AT)
        c.execute(
            "INSERT INTO life_days VALUES (?,?,?,?)", (str(AT.date()), "test", "{}", AT)
        )
        for item in plan:
            Itinerary._insert(c, item)

    engine.database.run_transaction(save)
    itinerary = Itinerary(
        engine.database, engine.schedule, engine.config, details=engine.details
    )
    assert (
        prioritize_meal_route(engine, itinerary, itinerary.current(AT), AT) is changed
    )
    saved = itinerary.day(AT)
    _continuous(saved)
    assert saved[0].ends_at == plan[0].ends_at
    assert saved[-1].ends_at == plan[-1].ends_at
    if changed:
        assert not any(item.location == "парк" for item in saved)
        meal = next(item for item in saved if item.kind == "dining")
        assert meal.location == venue["name"]
        assert meal.starts_at == plan[0].ends_at + timedelta(
            minutes=venue["routes"]["university"]
        )
        assert meal.ends_at - meal.starts_at == timedelta(minutes=40)
        assert engine.state()["appetite"]["last_meal_at"] is None
    else:
        assert saved == tuple(plan)
    assert not prioritize_meal_route(engine, itinerary, itinerary.current(AT), AT)
    assert itinerary.day(AT) == saved
