"""Named local timezone in Python; validated UTC text at storage boundaries."""

import math
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

ALMATY = ZoneInfo("Asia/Almaty")
_UTC_ISO = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)"
)


def now() -> datetime:
    """Return the current instant in Asia/Almaty."""
    return datetime.now(ALMATY)


def require_aware(value: datetime) -> datetime:
    """Reject naive values and normalize aware inputs to Asia/Almaty."""
    if not isinstance(value, datetime):
        raise TypeError("Expected a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware datetime is required")
    return value.astimezone(ALMATY)


def to_utc_iso(value: datetime) -> str:
    """Serialize without losing microseconds, using a canonical UTC Z suffix."""
    local = require_aware(value)
    return (
        local.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def from_utc_iso(value: str) -> datetime:
    """Read a UTC database timestamp into an aware Asia/Almaty datetime."""
    if not isinstance(value, str):
        raise TypeError("Expected a UTC ISO-8601 string")
    if _UTC_ISO.fullmatch(value) is None:
        raise ValueError("Expected a UTC ISO-8601 timestamp with Z or +00:00")
    return datetime.fromisoformat(value).astimezone(ALMATY)


def elapsed_hours(start: datetime, end: datetime) -> float:
    """Measure actual elapsed time, including a repeated local hour."""
    return (require_aware(end).timestamp() - require_aware(start).timestamp()) / 3600


def add_elapsed(value: datetime, *, hours: float = 0, minutes: float = 0) -> datetime:
    value = require_aware(value)
    if any(
        isinstance(part, bool) or not math.isfinite(part) for part in (hours, minutes)
    ):
        raise ValueError("Elapsed time must be finite")
    return datetime.fromtimestamp(
        value.timestamp() + hours * 3600 + minutes * 60, ALMATY
    )
