"""Admission is checked at execution and again at the transaction boundary."""

from datetime import datetime, timedelta

import pytest

from src.core.db import Database
from src.core.time_utils import ALMATY


def test_departure_rolls_back_learning_commit(tmp_path):
    from src.core.admission import StudyDeferred, StudyGate
    from src.core.itinerary import Activity, Itinerary

    at = datetime(2026, 9, 16, 20, tzinfo=ALMATY)
    clock = [at]
    database = Database(tmp_path / "admission.sqlite3")
    database.initialize()

    def setup(c):
        c.execute(
            "INSERT INTO life_days VALUES (?,?,?,?)", (str(at.date()), "test", "{}", at)
        )
        Itinerary._insert(
            c,
            Activity(
                "study",
                str(at.date()),
                at,
                at + timedelta(minutes=1),
                "дом",
                "study",
                "Study",
            ),
        )
        Itinerary._insert(
            c,
            Activity(
                "road",
                str(at.date()),
                at + timedelta(minutes=1),
                at + timedelta(hours=1),
                "транспорт",
                "travel",
                "Travel",
                origin="дом",
                destination="парк",
            ),
        )

    database.run_transaction(setup)
    gate = StudyGate(database, clock=lambda: clock[0])
    with pytest.raises(StudyDeferred), gate.session():

        def late(c):
            c.execute("INSERT INTO life_state VALUES ('study-test','1',?)", (at,))
            clock[0] = at + timedelta(minutes=2)

        database.run_transaction(late)
    with database.connection(readonly=True) as c:
        assert (
            c.execute("SELECT 1 FROM life_state WHERE key='study-test'").fetchone()
            is None
        )
    with pytest.raises(StudyDeferred), gate.session():
        pytest.fail("Away from home must not execute")


async def test_life_mood_effect_is_applied_once(tmp_path):
    from pathlib import Path

    from src.core.detailed_world import DetailedWorld
    from src.core.nutrition import Nutrition
    from src.core.time_utils import from_utc_iso
    from src.providers import RuntimeProviders

    at = datetime(2026, 9, 16, 18, 30, tzinfo=ALMATY)
    database = Database(tmp_path / "mood.sqlite3")
    database.initialize()
    providers = RuntimeProviders(database, Path("config"), clock=lambda: at)
    try:
        await providers.context(at)
        activity = providers.itinerary.current(at)
        providers.life.activity(activity, at)
        with database.connection(readonly=True) as c:
            assert not c.execute("SELECT 1 FROM life_effects").fetchone()
        world = DetailedWorld(providers.life)
        run = Nutrition(providers.life).home_meal(world, activity, at)
        assert run
        with database.connection(readonly=True) as c:
            later = from_utc_iso(
                c.execute(
                    "SELECT due_at FROM world_runs WHERE id=?", (run,)
                ).fetchone()[0]
            )
        world.advance(activity, later, seed=False)
        await providers.mood.apply_life_effect(later)
        await providers.mood.apply_life_effect(later + timedelta(seconds=1))
        with database.connection(readonly=True) as c:
            assert (
                c.execute(
                    "SELECT count(*) FROM life_effects WHERE applied_at IS NOT NULL"
                ).fetchone()[0]
                == 1
            )
            assert (
                c.execute(
                    "SELECT count(*) FROM mood WHERE last_event='life_meal'"
                ).fetchone()[0]
                == 1
            )
    finally:
        await providers.close()
