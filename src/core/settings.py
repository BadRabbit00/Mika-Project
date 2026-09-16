"""Read-only registry metadata, strict values, and explicit override storage."""

import json
import math
import re
from pathlib import Path
from typing import Protocol

from ruamel.yaml import YAML

from src.core.time_utils import now


class SettingsProvider(Protocol):
    """Consumers read settings at call time so overrides affect the next step."""

    def get(self, key: str): ...


class MissingSettingsStorage(RuntimeError):
    """No migrated database was attached to this settings provider."""


class SQLiteSettingsStore:
    """Read and write overrides installed by database migration 5."""

    def __init__(self, database):
        self.database = database
        with database.connection() as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(settings_overrides)")
            }
        if (
            not {"key", "value_json", "previous_json", "updated_at", "trace_id"}
            <= columns
        ):
            raise MissingSettingsStorage(
                "settings_overrides is not installed; initialize the database"
            )

    def get(self, key):
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM settings_overrides WHERE key=?", (key,)
            ).fetchone()
        return (
            None
            if row is None
            else {
                "value": json.loads(row["value_json"]),
                "previous": json.loads(row["previous_json"]),
                "at": row["updated_at"],
                "trace_id": row["trace_id"],
            }
        )

    def set(self, key, value, *, previous, at, trace_id):
        self.database.run_transaction(
            lambda c: c.execute(
                "INSERT INTO settings_overrides(key, value_json, previous_json, "
                "updated_at, trace_id) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "value_json=excluded.value_json, previous_json=excluded.previous_json, "
                "updated_at=excluded.updated_at, trace_id=excluded.trace_id",
                (
                    key,
                    json.dumps(value, allow_nan=False),
                    json.dumps(previous, allow_nan=False),
                    at,
                    trace_id,
                ),
            )
        )


class SettingsRegistry:
    def __init__(self, data, *, store=None):
        self.groups, self.entries = (
            data["groups"],
            {row["key"]: row for row in data["settings"]},
        )
        self.store = store

    @classmethod
    def from_file(cls, path: Path, *, store=None):
        return cls(
            YAML(typ="safe").load(Path(path).read_text(encoding="utf-8")), store=store
        )

    def get(self, key):
        entry = self.entries[key]
        override = self.store.get(key) if self.store is not None else None
        return override["value"] if override else entry.get("default")

    def validate(self, key, text):
        if key not in self.entries or self.entries[key].get("locked"):
            raise ValueError("Unknown or locked setting")
        entry = self.entries[key]
        match entry["type"]:
            case "int":
                if re.fullmatch(r"-?\d+", text) is None:
                    raise ValueError("An integer is required")
                value = int(text)
            case "float":
                value = float(text)
                if not math.isfinite(value):
                    raise ValueError("A finite number is required")
            case "bool":
                if text not in {"true", "false"}:
                    raise ValueError("Use true or false")
                value = text == "true"
            case "time":
                if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text):
                    raise ValueError("Use an HH:MM local wall-clock time")
                value = text
            case "enum" | "str":
                value = text
            case _:
                raise ValueError("Unsupported registry type")
        if "values" in entry and value not in entry["values"]:
            raise ValueError("Value is outside the declared choices")
        if "range" in entry and not entry["range"][0] <= value <= entry["range"][1]:
            raise ValueError("Value is outside the declared range")
        return round(value, 4) if type(value) is float else value

    def describe(self, key=None):
        if key in self.entries:
            entry = self.entries[key]
            if entry.get("locked"):
                raise ValueError("Locked settings are not exposed in menus")
            override = self.store.get(key) if self.store is not None else None
            return entry | {"current": self.get(key), "changed": override}
        return [
            self.describe(name)
            for name, row in self.entries.items()
            if not row.get("locked") and (key is None or row["group"] == key)
        ]

    def set(self, key, text, *, trace_id):
        value = self.validate(key, text)
        if self.store is None:
            raise MissingSettingsStorage(
                "Persistent overrides require a database-backed settings provider"
            )
        previous = self.get(key)
        self.store.set(key, value, previous=previous, at=now(), trace_id=trace_id)
        return {"key": key, "previous": previous, "current": value}

    def reset(self, key, *, trace_id):
        entry = self.entries[key]
        value = entry["default"]
        text = str(value).lower() if type(value) is bool else str(value)
        return self.set(key, text, trace_id=trace_id)
