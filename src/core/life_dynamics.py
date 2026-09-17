"""Authored productivity, deadline urgency, recovery and earned-pay rules."""

import math
from datetime import timedelta
from random import Random

from src.core.time_utils import from_utc_iso, require_aware, to_utc_iso


def urgency(elapsed_days, interval_days, exponent=6):
    """An exponential daily decision weight, bounded by zero and one."""
    if interval_days <= 0 or exponent <= 0:
        raise ValueError("An urgency interval and exponent must be positive")
    progress = max(0.0, min(1.0, elapsed_days / interval_days))
    return math.expm1(exponent * progress) / math.expm1(exponent)


def illness_stage(state, at):
    at = require_aware(at)
    if not state.get("ill_until"):
        return "well"
    end = from_utc_iso(state["ill_until"])
    start = from_utc_iso(state.get("ill_started", to_utc_iso(end - timedelta(days=3))))
    if at < start + timedelta(days=1):
        return "acute"
    if at < min(start + timedelta(days=3), end - timedelta(days=1)):
        return "weak"
    return "recovering" if at < end else "well"


def productivity(config, *, mood, debt, stage, hungry=False, workload_hours=0):
    cfg = config["productivity"]
    value = cfg["baseline"]
    for axis, weight in cfg["mood_weights"].items():
        value += mood[axis] * weight
    value -= max(0, debt) * cfg["debt_penalty_per_hour"]
    value -= cfg["hunger_penalty"] if hungry else 0
    value -= max(0, workload_hours) * cfg["workload_penalty_per_hour"]
    value = max(0, min(1, value)) * config["health"]["stages"][stage]["productivity"]
    return round(value, 4)


def study_minutes(config, value):
    if value < config["productivity"]["minimum_to_study"]:
        return 0
    low, high = config["productivity"]["study_minutes"]
    return round(low + (high - low) * value)


def earned_pay(config, value, identity):
    cfg = config["free_time"]
    factor = (
        Random("pay:" + identity).uniform(*cfg["low_productivity_pay_factor"])
        if value < cfg["low_productivity_threshold"]
        else 1
    )
    return {
        "base": cfg["side_job_pay"],
        "actual": round(cfg["side_job_pay"] * factor),
        "productivity": round(value, 4),
        "factor": round(factor, 4),
    }


def choose_free_time(config, needs, rng):
    if needs.get("clinic_due"):
        return "clinic"
    if needs.get("ill"):
        return "movie" if needs.get("productivity", 0) >= 0.2 else "rest"
    if needs.get("groceries") and needs["cash"] >= config["food"]["basket_cost"]:
        return "shop"
    if (
        needs.get("errands")
        and needs["cash"] >= config["free_time"]["errand_cash_minimum"]
    ):
        return "shop"
    if needs.get("laundry_due"):
        return "laundry"
    weights = dict(config["free_time"]["weights"])
    if needs.get("economize"):
        weights["side_job"] *= config["free_time"]["side_job_low_money_multiplier"]
        weights["cafe"] = 0
    if not needs.get("gym"):
        weights["gym"] = 0
    if needs.get("rain"):
        weights["walk"] = 0
    return rng.choices(list(weights), list(weights.values()), k=1)[0]
