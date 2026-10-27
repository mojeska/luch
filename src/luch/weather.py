"""Current weather at a position from Open-Meteo (free, no API key)."""
from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

API = "https://api.open-meteo.com/v1/forecast"
CACHE_SECONDS = 15 * 60

# WMO weather codes, short Russian words (English in i18n.py)
WMO_RU = {
    0: "ЯСНО", 1: "МАЛООБЛАЧНО", 2: "ПЕРЕМ. ОБЛАЧН.", 3: "ПАСМУРНО",
    45: "ТУМАН", 48: "ИЗМОРОЗЬ",
    51: "МОРОСЬ", 53: "МОРОСЬ", 55: "МОРОСЬ", 56: "ЛЕДЯНАЯ МОРОСЬ", 57: "ЛЕДЯНАЯ МОРОСЬ",
    61: "ДОЖДЬ", 63: "ДОЖДЬ", 65: "СИЛЬНЫЙ ДОЖДЬ", 66: "ЛЕДЯНОЙ ДОЖДЬ", 67: "ЛЕДЯНОЙ ДОЖДЬ",
    71: "СНЕГ", 73: "СНЕГ", 75: "СИЛЬНЫЙ СНЕГ", 77: "СНЕЖНАЯ КРУПА",
    80: "ЛИВЕНЬ", 81: "ЛИВЕНЬ", 82: "СИЛЬНЫЙ ЛИВЕНЬ", 85: "СНЕГОПАД", 86: "СНЕГОПАД",
    95: "ГРОЗА", 96: "ГРОЗА С ГРАДОМ", 99: "ГРОЗА С ГРАДОМ",
}
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


@dataclass
class Weather:
    temp: float
    feels: float
    humidity: int
    wind: float
    wind_dir: int
    code: int
    imperial: bool
    fetched: float

    @property
    def summary_ru(self) -> str:
        return WMO_RU.get(self.code, f"КОД {self.code}")

    @property
    def temp_unit(self) -> str:
        return "°F" if self.imperial else "°C"

    @property
    def wind_unit(self) -> str:
        return "mph" if self.imperial else "km/h"

    @property
    def compass(self) -> str:
        return COMPASS[round(self.wind_dir / 45) % 8]


def _fetch(lat: float, lon: float, imperial: bool) -> dict:
    q = {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "weather_code,wind_speed_10m,wind_direction_10m",
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": "mph" if imperial else "kmh",
    }
    req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "luch"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def parse(data: dict, imperial: bool) -> Weather:
    c = data["current"]
    return Weather(
        temp=c["temperature_2m"], feels=c["apparent_temperature"], humidity=round(c["relative_humidity_2m"]),
        wind=c["wind_speed_10m"], wind_dir=round(c["wind_direction_10m"]), code=int(c["weather_code"]),
        imperial=imperial, fetched=time.time(),
    )


class WeatherCache:
    """Refetches when the position moves about 1 km or the reading is 15 minutes old."""

    def __init__(self) -> None:
        self.key: tuple | None = None
        self.value: Weather | None = None

    async def get(self, lat: float, lon: float, imperial: bool) -> Weather:
        key = (round(lat, 2), round(lon, 2), imperial)
        if self.value and self.key == key and time.time() - self.value.fetched < CACHE_SECONDS:
            return self.value
        self.value = parse(await asyncio.to_thread(_fetch, lat, lon, imperial), imperial)
        self.key = key
        return self.value
