"""Context-local study admission, including a check immediately before commit."""

from contextlib import contextmanager
from contextvars import ContextVar

from src.core.time_utils import now, require_aware

_guard = ContextVar("study_guard", default=None)


class StudyDeferred(Exception):
    """The planned home-study interval ended; retry without advancing learning."""


def check_study(connection=None):
    guard = _guard.get()
    if guard is not None:
        guard(connection)


@contextmanager
def audit_scope():
    """Model cost/output receipts remain observable after a study interval ends."""
    token = _guard.set(None)
    try:
        yield
    finally:
        _guard.reset(token)


class StudyGate:
    def __init__(self, database, *, clock=now, location_provider=None):
        self.database, self.clock = database, clock
        self.location_provider = location_provider

    def check(self, connection=None):
        if connection is None:
            with self.database.connection(readonly=True) as c:
                return self.check(c)
        at = require_aware(self.clock())
        if self.location_provider is not None and self.location_provider(at) != "дом":
            raise StudyDeferred("The current location override is outside home")
        row = connection.execute(
            "SELECT id FROM life_activities WHERE state='active' AND starts_at<=? "
            "AND ends_at>? AND location='дом' AND kind='study' AND can_study=1 "
            "AND task_id IS NULL",
            (at, at),
        ).fetchone()
        if row is None:
            raise StudyDeferred("Waiting for an awake home-study interval")
        from src.core.detailed_world import DetailedWorld

        if DetailedWorld.occupied(connection, at):
            raise StudyDeferred("A physical world action is still in progress")
        if connection.execute(
            "SELECT 1 FROM sleep_log WHERE actual_bedtime<=? AND wake_at>? "
            "AND (origin!='override' OR override_until>? OR override_until IS NULL)",
            (at, at, at),
        ).fetchone():
            raise StudyDeferred("Sleeping prevents learning")

    @contextmanager
    def session(self):
        self.check()
        token = _guard.set(self.check)
        try:
            yield
            self.check()
        finally:
            _guard.reset(token)
