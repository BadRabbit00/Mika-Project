"""PAD observations for session exports, kept out of model context."""

import json
import math

from src.core.pad import AXES, Mood


def encode_mood(mood, model):
    if mood is None:
        return None
    if not isinstance(mood, Mood):
        raise TypeError("Session mood must be a validated PAD observation")
    return json.dumps(
        {
            "PAD": {axis: round(getattr(mood, axis), 4) for axis in AXES},
            "bands": {
                axis: model.band_for(axis, getattr(mood, axis))["id"] for axis in AXES
            },
        },
        allow_nan=False,
    )


def mood_metrics(session, turns):
    samples = []
    for raw in [
        session["mood_start"],
        *(row.get("mood") for row in turns),
        session["mood_end"],
    ]:
        if not raw:
            continue
        try:
            value = json.loads(raw)
            Mood(**value["PAD"])
            if set(value["bands"]) != set(AXES):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        samples.append(value)
    if len(samples) < 2:
        return dict(
            mood_drift=None,
            mood_delta=None,
            band_changes=None,
            mood_drift_status="insufficient_observations",
        )
    delta = {axis: samples[-1]["PAD"][axis] - samples[0]["PAD"][axis] for axis in AXES}
    drift = math.sqrt(sum(value**2 for value in delta.values())) / (2 * math.sqrt(3))
    changes = sum(
        left["bands"][axis] != right["bands"][axis]
        for left, right in zip(samples, samples[1:], strict=False)
        for axis in AXES
    )
    return dict(
        mood_drift=drift,
        mood_delta={axis: round(value, 4) for axis, value in delta.items()},
        band_changes=changes,
        mood_drift_status="measured",
    )
