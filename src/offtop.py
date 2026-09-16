"""Choose configured life events and commit their continuity after publication."""

import itertools
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from random import Random
from types import MappingProxyType

import structlog
from ruamel.yaml import YAML

from src.core.pad import finite
from src.core.settings import SettingsRegistry
from src.core.time_utils import from_utc_iso, require_aware, to_utc_iso
from src.writer import WriteResult

log = structlog.get_logger("blogai.offtop")
_FIELDS = re.compile(r"\{([^{}]+)\}")


class OfftopHistoryError(ValueError):
    """The supplied history cannot support deterministic repeat prevention."""


def make_entity(slot, values):
    return json.dumps(
        {"slot": slot, "values": dict(values)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class OfftopEvent:
    at: datetime
    slot: str
    frame: str
    values: dict
    entity: str
    text: str
    progress: tuple[tuple[str, int, int], ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "at", require_aware(self.at))
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


class OfftopPlanner:
    def __init__(
        self,
        database,
        life,
        *,
        max_slot_uses: int | None = None,
        slot_weight=None,
        settings=None,
    ):
        if max_slot_uses is not None and (
            type(max_slot_uses) is not int or max_slot_uses <= 0
        ):
            raise ValueError("An explicit positive slot-use limit is required")
        self.database, self.life = database, life
        self._limit_override, self._weight_override = max_slot_uses, slot_weight
        self.settings = settings or SettingsRegistry.from_file("config/settings.yaml")
        for slot in life["slots"]:
            signatures = [frozenset(_FIELDS.findall(frame)) for frame in slot["frames"]]
            if len(signatures) != len(set(signatures)):
                raise OfftopHistoryError("Ambiguous frame signatures")

    @classmethod
    def from_config(cls, database, directory: Path, **policy):
        life = YAML(typ="safe").load(
            (Path(directory) / "life.yaml").read_text(encoding="utf-8")
        )
        return cls(database, life, **policy)

    @property
    def max_slot_uses(self):
        return self._limit_override or self.settings.get("offtop.max_slot_uses")

    def slot_weight(self, slot, count):
        if self._weight_override is not None:
            return self._weight_override(slot, count)
        return slot["weight"] / (count + self.settings.get("offtop.slot_smoothing"))

    def _progress_value(self, key, state):
        if key in state:
            return int(state[key])
        group, field = key.split(".")
        config = self.life["progress"][group]
        return (
            config["stages"].index(config["state"])
            if field == "stage"
            else config[field]
        )

    def _snapshot(self, at):
        with self.database.connection() as connection:
            connection.execute("BEGIN")
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT at, slot, entity FROM life_journal WHERE at<=? "
                    "ORDER BY at, id",
                    (to_utc_iso(at),),
                )
            ]
            state = {
                row[0]: row[1]
                for row in connection.execute("SELECT key, value FROM life_state")
            }
            connection.execute("COMMIT")
        cutoff = at - timedelta(days=self.life["rules"]["entity_cooldown_days"])
        for row in rows:
            row["at"] = from_utc_iso(row["at"])
            try:
                parsed = json.loads(row["entity"])
                if parsed["slot"] != row["slot"] or not isinstance(
                    parsed["values"], dict
                ):
                    raise ValueError("Invalid event fingerprint")
                row["values"] = parsed["values"]
            except (ValueError, TypeError, KeyError) as error:
                if row["at"].timestamp() >= cutoff.timestamp():
                    raise OfftopHistoryError(
                        "Legacy event history has no canonical entity"
                    ) from error
                row["values"] = {}
        return rows, state

    def pick(
        self,
        at: datetime,
        *,
        rng: Random,
        week_start: datetime,
        is_exam_day: bool,
        people_labels: dict[str, str],
        weather: str | None,
        bindings: dict[str, str] | None = None,
    ) -> OfftopEvent | None:
        at, week_start = require_aware(at), require_aware(week_start)
        if (
            not week_start.timestamp()
            <= at.timestamp()
            < (week_start + timedelta(days=7)).timestamp()
        ):
            raise ValueError("Selection time must belong to the explicit week")
        if is_exam_day and self.life["rules"]["never_on_exam_day"]:
            return None
        history, state = self._snapshot(at)
        if (
            sum(row["at"].timestamp() >= week_start.timestamp() for row in history)
            >= self.life["rules"]["max_per_week"]
        ):
            return None
        window = at - timedelta(days=self.life["rules"]["slot_window_days"])
        recent = [
            row
            for row in history
            if row["at"].timestamp()
            >= (
                at - timedelta(days=self.life["rules"]["entity_cooldown_days"])
            ).timestamp()
        ]
        choices, weights = [], []
        for slot in self.life["slots"]:
            uses = [row for row in history if row["slot"] == slot["id"]]
            count = sum(row["at"].timestamp() >= window.timestamp() for row in uses)
            if count >= self.max_slot_uses or (
                slot.get("weekday_only") and at.weekday() >= 5
            ):
                continue
            if (
                uses
                and uses[-1]["at"].timestamp()
                > (at - timedelta(days=slot["cooldown_days"])).timestamp()
            ):
                continue
            frames = []
            for frame in slot["frames"]:
                candidates, progress = self._candidates(
                    slot, frame, state, recent, people_labels, weather, bindings or {}
                )
                if candidates:
                    signature = set(_FIELDS.findall(frame))
                    last = max(
                        (
                            row["at"].timestamp()
                            for row in uses
                            if set(row["values"]) == signature
                        ),
                        default=float("-inf"),
                    )
                    frames.append((last, frame, candidates, progress))
            if frames:
                weight = finite(self.slot_weight(slot, count))
                if weight <= 0:
                    raise ValueError("Slot weights must be positive")
                choices.append((slot, frames))
                weights.append(weight)
        if not choices:
            log.info("offtop_no_available_event", at=to_utc_iso(at))
            return None
        slot, frames = rng.choices(choices, weights=weights, k=1)[0]
        least = min(frame[0] for frame in frames)
        _, frame, candidates, progress = rng.choice(
            [frame for frame in frames if frame[0] == least]
        )
        values = rng.choice(candidates)
        event = OfftopEvent(
            at,
            slot["id"],
            frame,
            values,
            make_entity(slot["id"], values),
            _FIELDS.sub(lambda match: values[match[1]], frame),
            tuple(progress),
        )
        log.info(
            "offtop_event_selected",
            slot=event.slot,
            entity=event.entity,
            at=to_utc_iso(at),
        )
        return event

    def _candidates(self, slot, frame, state, recent, people_labels, weather, bindings):
        fields = list(dict.fromkeys(_FIELDS.findall(frame)))
        options, progress = [], []
        progress_ref = next(
            (
                name.split(":", 1)[1].split(".")[0]
                for name in fields
                if name.startswith("progress:")
            ),
            None,
        )
        labels = {
            f"{person['id']}.{case}": value
            for person in self.life["people"]
            for case, value in person.get("ref", {}).items()
        } | people_labels

        def references(value):
            fields = _FIELDS.findall(value)
            if any(
                not field.startswith("people:") or field[7:] not in labels
                for field in fields
            ):
                return None
            return _FIELDS.sub(lambda match: labels[match[1][7:]], value)

        for name in fields:
            if name.startswith("people:"):
                value = labels.get(name.split(":", 1)[1])
                options.append([value] if value else [])
            elif name.startswith("progress:"):
                group, key = name.split(":", 1)[1].split(".", 1)
                options.append([str(self.life["progress"][group][key])])
            elif name == "ep":
                if progress_ref is None:
                    raise ValueError("Episode frame has no progress reference")
                key = f"{progress_ref}.episode"
                current = self._progress_value(key, state)
                options.append(
                    [str(current)]
                    if 1 <= current <= self.life["progress"][progress_ref]["total"]
                    else []
                )
                progress.append((key, current, current + 1))
            elif name == "weather":
                options.append([weather] if weather else [])
            elif name in slot.get("vars", {}):
                used = {row["values"].get(name) for row in recent}
                options.append(
                    [
                        resolved
                        for value in slot["vars"][name]
                        if (resolved := references(str(value))) is not None
                        and resolved not in used
                    ]
                )
            elif name in bindings:
                options.append([bindings[name]])
            elif name in slot.get("bindings", {}):
                binding = slot["bindings"][name]
                if binding["from"] != "progress":
                    raise ValueError("Unsupported configured binding source")
                group, field = binding["key"].split(".")
                if field != "stages":
                    raise ValueError("A progress binding must reference stages")
                stages, key = self.life["progress"][group][field], f"{group}.stage"
                current = self._progress_value(key, state)
                if not 0 <= current < len(stages):
                    raise ValueError("Progress stage is outside its configured range")
                options.append([stages[current]])
                progress.append((key, current, min(current + 1, len(stages) - 1)))
            else:
                log.warning(
                    "offtop_frame_unavailable",
                    field=name,
                    slot=slot["id"],
                )
                return [], progress
        entities = {row["entity"] for row in recent}
        candidates = [
            dict(zip(fields, values, strict=True))
            for values in itertools.product(*options)
        ]
        return [
            value
            for value in candidates
            if make_entity(slot["id"], value) not in entities
        ], progress

    def remember(self, event: OfftopEvent, *, published_at: datetime, text: str) -> int:
        published_at = require_aware(published_at)
        if published_at.timestamp() < event.at.timestamp() or not text.strip():
            raise ValueError("Publication must follow selection and contain text")
        if event.entity != make_entity(event.slot, event.values):
            raise ValueError("Event fingerprint does not match its values")

        def save(connection):
            previous = connection.execute(
                "SELECT id, text FROM life_journal WHERE entity=? AND at>=? "
                "ORDER BY at DESC LIMIT 1",
                (
                    event.entity,
                    to_utc_iso(
                        published_at
                        - timedelta(days=self.life["rules"]["entity_cooldown_days"])
                    ),
                ),
            ).fetchone()
            if previous:
                if previous["text"] != text:
                    raise ValueError("Event entity was already published")
                return previous["id"]
            for key, before, after in event.progress:
                row = connection.execute(
                    "SELECT value FROM life_state WHERE key=?", (key,)
                ).fetchone()
                current = self._progress_value(key, {key: row[0]} if row else {})
                if current != before:
                    raise ValueError("Progress changed since event selection")
                connection.execute(
                    "INSERT INTO life_state(key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "updated_at=excluded.updated_at",
                    (key, str(after), published_at),
                )
            return connection.execute(
                "INSERT INTO life_journal(at, slot, entity, text) VALUES (?, ?, ?, ?)",
                (published_at, event.slot, event.entity, text),
            ).lastrowid

        row_id = self.database.run_transaction(save)
        log.info(
            "offtop_event_remembered",
            journal_id=row_id,
            slot=event.slot,
            at=to_utc_iso(published_at),
        )
        return row_id


@dataclass(frozen=True)
class OfftopDraft:
    result: WriteResult
    event: OfftopEvent | None


class OfftopGenerator:
    """Compose weather, event selection, isolated context, and draft validation."""

    def __init__(self, planner, world, writer, weather_client):
        self.planner, self.world = planner, world
        self.writer, self.weather_client = writer, weather_client

    async def generate(
        self,
        *,
        day,
        mood,
        wake_reason,
        week_start,
        is_exam_day,
        people_labels,
        last_weather_mention_at,
        rng,
        bindings=None,
    ):
        if day.blackout.blocked:
            return OfftopDraft(
                WriteResult("", "blocked", None, 0, (day.blackout.reason,)), None
            )
        weather = await self.world.relevant_weather(
            day.at,
            client=self.weather_client,
            location=day.location,
            last_mention_at=last_weather_mention_at,
            rng=rng,
        )
        weather_data = weather.context_data() if weather is not None else None
        event = self.planner.pick(
            day.at,
            rng=rng,
            week_start=week_start,
            is_exam_day=is_exam_day,
            people_labels=people_labels,
            bindings=bindings,
            weather=json.dumps(weather_data, ensure_ascii=False)
            if weather_data
            else None,
        )
        if event is None:
            return OfftopDraft(WriteResult("", "no_event", None, 0), None)
        result = await self.writer.generate(
            "offtop",
            day=day,
            mood=mood,
            wake_reason=wake_reason,
            offtop_event={"event": event.text, "weather": weather_data},
        )
        return OfftopDraft(result, event)
