"""Deterministic checks for authored activity, intention and purchase claims."""

import json
import re
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from ruamel.yaml import YAML

from src.core.itinerary import Activity
from src.core.time_utils import from_utc_iso, require_aware
from src.core.transitions import activity_data


@lru_cache(maxsize=8)
def _rules(path):
    return YAML(typ="safe").load(Path(path))["claims"]


def plan_evidence(database, at):
    at = require_aware(at)
    with database.connection(readonly=True) as c:
        rows = c.execute(
            "SELECT * FROM life_activities WHERE state='active' AND ends_at>? "
            "AND starts_at<? ORDER BY starts_at LIMIT 7",
            (at, at + timedelta(hours=2)),
        ).fetchall()
    values = [activity_data(Activity.from_row(row)) for row in rows]
    current = next(
        (item for item in values if from_utc_iso(item["starts_at"]) <= at), None
    )
    return {"current": current, "planned": [item for item in values if item != current]}


def activity_conflicts(text, evidence, config_path):
    """Future plans do not establish present actions; prose never creates facts."""
    rules = _rules(str(config_path))
    current = evidence.get("current") or {}
    planned = evidence.get("planned", [])
    history = [
        item
        for change in evidence.get("transitions", [])
        for item in (change.get("previous"), change.get("current"))
        if item
    ]
    facts = json.dumps(
        {
            key: evidence.get(key)
            for key in ("facts", "related_event", "preceding_events", "retrospective")
        },
        ensure_ascii=False,
    ).casefold()
    reasons = []
    for sentence in re.split(r"[.!?;\n]+|,\s*(?:а|но)\s+", text.casefold()):
        past = bool(
            re.search(
                r"\b(?:пила|попила|заварила|приготовила|встретилась|была|сходила|ходила|вернулась|раньше|вчера|закончил[аи]|после|had|was|finished)\b",
                sentence,
            )
        )
        future = bool(
            re.search(
                r"\b(?:пойду|поеду|собираюсь|планирую|буду|завтра|потом|позже|встретимся|will)\b",
                sentence,
            )
        )
        if re.search(r"\b(?:сейчас|иду|еду|пью|делаю|начинаю|возвращаюсь)\b", sentence):
            past = False
            future = False
        permitted = [current] + (planned if future else history if past else [])
        for name, rule in rules.items():
            match = re.search(rule["pattern"], sentence, re.I)
            if not match:
                continue
            if re.search(r"\b(?:не|not)\s*$", sentence[: match.start()]):
                continue
            supported = any(item.get("kind") in rule["kinds"] for item in permitted)
            if past and re.search(rule["pattern"], facts, re.I):
                supported = True
            if not supported and current.get("kind") == "travel":
                supported = current.get("destination") in rule.get("destinations", [])
            if name in {"meeting", "restaurant", "ice_cream", "purchase"}:
                # A route or generic venue cannot prove attendance or an order.
                supported = bool(re.search(rule["pattern"], facts, re.I))
            if rule.get("recorded_only"):
                supported = bool(re.search(rule["evidence_pattern"], facts, re.I))
            if not supported:
                reasons.append("unplanned_" + name)
        if re.search(
            r"(?:закончил[аи]|прекратила|бросила).{0,35}уч[её]б.{0,35}(?:устал|сил)|(?:устал|сил).{0,35}(?:закончил|прекратил)",
            sentence,
        ):
            if not any(
                change.get("reason") == "fatigue"
                for change in evidence.get("transitions", [])
            ):
                reasons.append("unsupported_fatigue_reason")
    return tuple(dict.fromkeys(reasons))


def compact_evidence(value):
    """Remove storage identifiers from the model's factual transition input."""
    if isinstance(value, list):
        return [compact_evidence(item) for item in value]
    if isinstance(value, dict):
        return {
            key: compact_evidence(item)
            for key, item in value.items()
            if key
            not in {
                "id",
                "day",
                "revision",
                "task_id",
                "related_event_id",
                "source_activity_id",
            }
            and item is not None
        }
    return value
