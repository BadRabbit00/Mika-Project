"""Realistic outing durations and persisted day/activity/event containment trees."""

import hashlib
import json
from datetime import timedelta

from src.core.time_utils import local_clock, require_aware, to_utc_iso


def outing_segments(at, limit, rng, config, details, *, cafe_allowed):
    """Return a full outing, including both journeys, or no outing if it cannot fit."""
    at, limit = require_aware(at), require_aware(limit)
    spec = details["outings"]
    duration = rng.randint(*spec["duration_minutes"])
    if at + timedelta(minutes=duration) > limit:
        return ()
    road = config["itinerary"]["locations"]["road"]
    walk = rng.randint(*spec["park_walk_minutes"])
    rest = rng.randint(*spec["park_rest_minutes"])
    legs = [
        (20, road, "travel", "Дорога в парк", "дом", "парк"),
        (walk, "парк", "walk", "Прогулка по аллеям", None, None),
        (rest, "парк", "park_rest", "Отдых у фонтана", None, None),
    ]
    if rng.random() < details["routes"]["obstruction_chance"]:
        delay = rng.randint(*details["routes"]["extra_minutes"])
        reason = rng.choice(details["routes"]["obstruction_reasons"])
        first = legs[0]
        legs[0] = (
            first[0] + delay,
            first[1],
            first[2],
            first[3] + ": " + reason,
            first[4],
            first[5],
        )
    route = rng.randint(*details["routes"]["park_cafe_minutes"])
    stop = rng.randint(*spec["cafe_minutes"])
    has_cafe = cafe_allowed and rng.random() < spec["cafe_chance"]
    if has_cafe and sum(leg[0] for leg in legs) + route + stop + 15 <= duration:
        legs.extend(
            [
                (route, road, "travel", "Пешком из парка в кофейню", "парк", "кофейня"),
                (stop, "кофейня", "cafe", "Отдых в кофейне", None, None),
                (15, road, "travel", "Пешком домой", "кофейня", "дом"),
            ]
        )
    else:
        legs.append((20, road, "travel", "Пешком домой", "парк", "дом"))
    spare = duration - sum(item[0] for item in legs)
    if spare < 0:
        return ()
    legs[2] = (legs[2][0] + spare, *legs[2][1:])
    cursor, result = at, []
    for minutes, place, kind, label, origin, destination in legs:
        end = cursor + timedelta(minutes=minutes)
        if place in details["venues"]:
            venue = details["venues"][place]
            if cursor < local_clock(cursor, venue["opens"]) or end > local_clock(
                cursor, venue["closes"]
            ):
                return ()
        result.append(
            dict(
                minutes=minutes,
                location=place,
                kind=kind,
                label=label,
                origin=origin,
                destination=destination,
            )
        )
        cursor = end
    return tuple(result)


def plan_tree(activities):
    """Group the saved leaves; no location, travel or duration is invented here."""
    if not activities:
        return None
    root = {
        "kind": "day",
        "starts_at": to_utc_iso(activities[0].starts_at),
        "ends_at": to_utc_iso(activities[-1].ends_at),
        "children": [],
    }
    awake, group, excursion = None, None, None
    for item in activities:
        leaf = {
            "id": item.id,
            "kind": item.kind,
            "location": item.location,
            "label": item.label,
            "subject": item.subject,
            "origin": item.origin,
            "destination": item.destination,
            "starts_at": to_utc_iso(item.starts_at),
            "ends_at": to_utc_iso(item.ends_at),
        }
        if item.kind == "sleep":
            root["children"].append(leaf)
            awake, group, excursion = None, None, None
            continue
        if awake is None:
            awake = {
                "kind": "awake",
                "starts_at": leaf["starts_at"],
                "ends_at": leaf["ends_at"],
                "children": [],
            }
            root["children"].append(awake)
        if item.kind == "travel" and item.origin == "дом":
            excursion = (
                "university"
                if item.destination in {"универ", "остановка у дома"}
                else "recreation"
            )
        category = excursion or (
            "home_study"
            if item.kind
            in {
                "study",
                "tea_prepare",
                "tea_break",
                "short_rest",
                "food_prepare",
                "food_break",
            }
            else "home_life"
        )
        if group is None or group["kind"] != category:
            group = {
                "kind": category,
                "starts_at": leaf["starts_at"],
                "ends_at": leaf["ends_at"],
                "children": [],
            }
            awake["children"].append(group)
        group["children"].append(leaf)
        group["ends_at"] = awake["ends_at"] = leaf["ends_at"]
        if item.kind == "travel" and item.destination == "дом":
            excursion, group = None, None
    return root


def save_plan(database, activities, at):
    tree = plan_tree(activities)
    if tree is None:
        return None
    leaves = {}

    def collect(node):
        if "id" in node:
            leaves[node["id"]] = node
        for child in node.get("children", []):
            collect(child)

    collect(tree)
    with database.connection(readonly=True) as c:
        for row in c.execute(
            "SELECT id,node,starts_at,ends_at,payload FROM world_steps "
            "WHERE starts_at>=? AND ends_at<=? ORDER BY starts_at",
            (activities[0].starts_at, activities[-1].ends_at),
        ):
            data = json.loads(row["payload"])
            leaf = leaves.get(data["parent_activity_id"])
            if (
                leaf
                and leaf["starts_at"]
                <= row["starts_at"]
                < row["ends_at"]
                <= leaf["ends_at"]
            ):
                leaf.setdefault("children", []).append(
                    {
                        "id": row["id"],
                        "kind": "event",
                        "node": row["node"],
                        "starts_at": row["starts_at"],
                        "ends_at": row["ends_at"],
                        "outcome": data["outcome"],
                    }
                )
    payload = json.dumps(tree, ensure_ascii=False, sort_keys=True)
    identity = hashlib.sha256(payload.encode()).hexdigest()
    database.run_transaction(
        lambda c: c.execute(
            "INSERT OR IGNORE INTO world_plans VALUES (?,?,?,?)",
            (identity, str(require_aware(at).date()), at, payload),
        )
    )
    return tree
