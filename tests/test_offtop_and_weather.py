"""Configured event selection and Open-Meteo boundaries without live network calls."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from random import Random
from unittest.mock import Mock

import httpx
import pytest
from ruamel.yaml import YAML

from src.core.db import Database
from src.core.time_utils import ALMATY
from src.core.weather import WeatherClient, weather_relevant
from src.offtop import OfftopPlanner

AT = datetime(2026, 9, 16, 19, tzinfo=ALMATY)


@pytest.fixture
def planner(tmp_path):
    database = Database(tmp_path / "offtop.sqlite3")
    database.initialize()
    life = YAML(typ="safe").load(Path("config/life.yaml").read_text())
    return OfftopPlanner(database, life, max_slot_uses=2,
                         slot_weight=lambda slot, uses: slot["weight"] / max(1, uses))


def pick(planner, at=AT, **kwargs):
    return planner.pick(at, rng=Random(4), week_start=at - timedelta(days=2),
                        is_exam_day=False, people_labels={}, weather=None, **kwargs)


def test_offtop_pick_is_read_only_and_commits_only_after_publication(planner):
    event = pick(planner)
    assert event is not None and event.text and event.entity
    with planner.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM life_journal").fetchone()[0] == 0
    row_id = planner.remember(event, published_at=AT, text="Published event text.")
    assert planner.remember(event, published_at=AT, text="Published event text.") == row_id
    with planner.database.connection() as connection:
        row = connection.execute("SELECT * FROM life_journal").fetchone()
        assert row["entity"] == event.entity and row["at"].endswith("Z")


def test_offtop_cooldown_week_limit_and_entity_dedup(planner):
    first = pick(planner)
    planner.remember(first, published_at=AT, text="First post.")
    second = pick(planner, AT + timedelta(hours=1))
    assert second.slot != first.slot and second.entity != first.entity
    planner.remember(second, published_at=AT + timedelta(hours=1), text="Second post.")
    assert pick(planner, AT + timedelta(hours=2)) is None


def test_offtop_skips_weekday_slot_on_weekends_and_exam_days(planner):
    saturday = AT + timedelta(days=3)
    planner.life["slots"] = [slot for slot in planner.life["slots"] if slot.get("weekday_only")]
    assert pick(planner, saturday) is None
    assert planner.pick(AT, rng=Random(1), week_start=AT - timedelta(days=2),
                        is_exam_day=True, people_labels={}, weather=None) is None


def test_offtop_lru_and_unused_variables_use_persisted_entity_data(planner):
    planner.life["slots"] = [slot for slot in planner.life["slots"] if slot["id"] == "eda"]
    first = pick(planner)
    planner.remember(first, published_at=AT, text="First food post.")
    second = pick(planner, AT + timedelta(days=7))
    assert second.frame != first.frame
    assert second.values["dish"] != first.values["dish"]
    assert json.loads(first.entity)["values"] == dict(first.values)


def test_offtop_progress_advances_after_publication_only(planner):
    slot = next(slot for slot in planner.life["slots"] if slot["id"] == "dosug")
    slot["frames"] = [slot["frames"][0]]
    planner.life["slots"] = [slot]
    first = pick(planner)
    assert first.values["ep"] == "1"
    planner.remember(first, published_at=AT, text="Finished an episode.")
    planner.remember(first, published_at=AT, text="Finished an episode.")
    second = pick(planner, AT + timedelta(days=7))
    assert second.values["ep"] == "2"


async def test_weather_uses_aware_unix_time_and_cache():
    paths = []
    def handle(request):
        paths.append(request.url.path)
        if request.url.path.endswith("search"):
            return httpx.Response(200, json={"results": [{"latitude": 43.25, "longitude": 76.95,
                                                          "country_code": "KZ", "timezone": "Asia/Almaty"}]})
        assert request.url.params["timeformat"] == "unixtime"
        return httpx.Response(200, json={"current": {"time": int(AT.timestamp()),
            "temperature_2m": 32, "weather_code": 0, "precipitation": 0, "wind_speed_10m": 3}})
    async with WeatherClient.from_config(Path("config"), transport=httpx.MockTransport(handle)) as client:
        weather = await client.fetch(AT)
        assert weather.at == AT and weather.at.tzinfo == ALMATY and weather.is_extreme
        assert await client.fetch(AT + timedelta(minutes=29)) == weather
    assert paths == ["/v1/search", "/v1/forecast"]


async def test_weather_api_failure_uses_configured_seasonal_fallback():
    def handle(request):
        raise httpx.ConnectError("Fixture failure", request=request)
    async with WeatherClient.from_config(Path("config"), transport=httpx.MockTransport(handle)) as client:
        weather = await client.fetch(AT, rng=Random(1))
        assert weather.source == "seasonal" and weather.at == AT
        choices = YAML(typ="safe").load(Path("config/life.yaml").read_text())["weather"][9]
        assert weather.seasonal_text in choices
        assert await client.fetch(AT.replace(month=7), rng=Random(1)) is None


async def test_weather_relevance_preserves_cooldown_and_location_rules():
    def handle(request):
        raise httpx.ConnectError("Fixture failure", request=request)
    async with WeatherClient.from_config(Path("config"), transport=httpx.MockTransport(handle)) as client:
        weather = await client.fetch(AT, rng=Random(1))
    rng = Mock()
    rng.random.return_value = 0.39
    assert not weather_relevant(weather, outdoors=True, last_mention_days=2, rng=rng)
    assert weather_relevant(weather, outdoors=True, last_mention_days=3, rng=rng)
    assert not weather_relevant(weather, outdoors=False, last_mention_days=3, rng=rng)
    rng.random.return_value = 0.149
    assert weather_relevant(weather, outdoors=False, last_mention_days=3, rng=rng)
