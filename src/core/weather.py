"""Open-Meteo observations, bounded caching, and supplied seasonal fallback."""

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from random import Random

import httpx
import structlog
from ruamel.yaml import YAML

from src.core.pad import finite
from src.core.time_utils import ALMATY, elapsed_hours, require_aware

log = structlog.get_logger("blogai.weather")


@dataclass(frozen=True)
class Weather:
    at: datetime
    source: str
    temperature_c: float | None = None
    weather_code: int | None = None
    precipitation_mm: float | None = None
    wind_kmh: float | None = None
    seasonal_text: str | None = None
    changed_sharply: bool = False

    def __post_init__(self):
        object.__setattr__(self, "at", require_aware(self.at))
        for value in (self.temperature_c, self.precipitation_mm, self.wind_kmh):
            if value is not None:
                finite(value)

    @property
    def is_extreme(self):
        return (
            self.temperature_c is not None
            and (self.temperature_c >= 32 or self.temperature_c <= -20)
        ) or (self.weather_code in {65, 67, 82, 95, 96, 99})

    def context_data(self):
        return {
            key: value
            for key, value in asdict(self).items()
            if key not in {"at", "changed_sharply", "source"} and value is not None
        }


class WeatherClient:
    def __init__(self, life: dict, settings: dict, *, transport=None):
        self.life, self.settings = life, settings
        self.client = httpx.AsyncClient(
            transport=transport, timeout=15, trust_env=False
        )
        self._coordinates = None
        self._cached = self._cached_at = None

    @classmethod
    def from_config(cls, directory: Path, *, transport=None):
        yaml = YAML(typ="safe")
        life = yaml.load((Path(directory) / "life.yaml").read_text(encoding="utf-8"))
        registry = yaml.load(
            (Path(directory) / "settings.yaml").read_text(encoding="utf-8")
        )
        settings = {
            item["key"]: item["default"]
            for item in registry["settings"]
            if item["key"] in {"world.weather_source", "world.weather_chance"}
        }
        return cls(life, settings, transport=transport)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def fetch(self, at: datetime, *, rng: Random | None = None) -> Weather | None:
        at = require_aware(at)
        source = self.settings["world.weather_source"]
        if source == "off":
            return None
        if (
            self._cached_at is not None
            and 0 <= elapsed_hours(self._cached_at, at) * 3600 < 1800
        ):
            return self._cached
        weather = None
        if source == "api":
            try:
                weather = await self._fetch_api()
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
                log.warning("weather_api_unavailable", error=str(error))
        if weather is None:
            choices = self.life["weather"].get(at.month, [])
            if choices:
                weather = Weather(
                    at, "seasonal", seasonal_text=(rng or Random()).choice(choices)
                )
                log.info("weather_seasonal_fallback", month=at.month)
            else:
                log.warning(
                    "weather_missing_fallback", month=at.month, todo="WEATHER-MONTHS"
                )
        self._cached, self._cached_at = weather, at
        return weather

    async def _fetch_api(self):
        if self._coordinates is None:
            response = await self.client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": self.life["identity"]["city"],
                    "countryCode": "KZ",
                    "count": 1,
                },
            )
            response.raise_for_status()
            candidates = response.json()["results"]
            if len(candidates) != 1:
                raise ValueError("Expected one Almaty geocoding result")
            place = candidates[0]
            if place["country_code"] != "KZ" or place["timezone"] != "Asia/Almaty":
                raise ValueError("Geocoding did not resolve Almaty")
            latitude, longitude = finite(place["latitude"]), finite(place["longitude"])
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise ValueError("Invalid weather coordinates")
            self._coordinates = {"latitude": latitude, "longitude": longitude}
        response = await self.client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                **self._coordinates,
                "current": "temperature_2m,weather_code,precipitation,wind_speed_10m",
                "hourly": "temperature_2m",
                "past_days": 1,
                "forecast_days": 1,
                "timeformat": "unixtime",
                "timezone": "GMT",
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
                "precipitation_unit": "mm",
            },
        )
        response.raise_for_status()
        data = response.json()
        current = data["current"]
        at = datetime.fromtimestamp(finite(current["time"]), ALMATY)
        temperature = finite(current["temperature_2m"])
        code = current["weather_code"]
        if type(code) is not int or not 0 <= code <= 99:
            raise ValueError("Invalid weather condition code")
        hourly = data.get("hourly", {})
        target = (
            int(at.replace(minute=0, second=0, microsecond=0).timestamp()) - 24 * 3600
        )
        yesterday = dict(
            zip(hourly.get("time", []), hourly.get("temperature_2m", []), strict=True)
        ).get(target)
        return Weather(
            at,
            "api",
            temperature,
            code,
            finite(current["precipitation"]),
            finite(current["wind_speed_10m"]),
            changed_sharply=yesterday is not None
            and abs(temperature - finite(yesterday)) >= 15,
        )


def weather_relevant(
    weather: Weather | None,
    *,
    outdoors: bool,
    last_mention_days: float,
    rng: Random,
    ordinary_chance: float = 0.15,
    cooldown_days: int = 3,
) -> bool:
    if weather is None or last_mention_days < cooldown_days:
        return False
    if weather.is_extreme:
        return True
    if weather.changed_sharply:
        return True
    if outdoors:
        return rng.random() < 0.4
    return rng.random() < ordinary_chance
