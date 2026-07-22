"""Weather with three-tier fallback: qweather -> Open-Meteo -> climate zone.

Never returns None, never raises.
"""

from __future__ import annotations

import datetime
import hashlib
import math
import os
import random
from typing import Any, Final

from nowhere import providers

# ── QWeather key ───────────────────────────────────────────────────
_QWEATHER_KEY: str = os.environ.get("NOWHERE_QWEATHER_KEY", "")

# ── WMO weather code -> (precip, chinese_text) ─────────────────────
# https://www.noaa.gov/weather/wmo-weather-interpretation-codes
_WMO_MAP: Final[dict[int, tuple[str, str]]] = {
    0: ("none", "晴"),
    1: ("none", "大部晴"),
    2: ("none", "多云"),
    3: ("none", "阴"),
    45: ("none", "雾"),
    48: ("none", "冻雾"),
    51: ("rain", "小毛毛雨"),
    53: ("rain", "毛毛雨"),
    55: ("rain", "大毛毛雨"),
    56: ("rain", "冻毛毛雨"),
    57: ("rain", "冻毛毛雨"),
    61: ("rain", "小雨"),
    63: ("rain", "中雨"),
    65: ("rain", "大雨"),
    66: ("rain", "冻雨"),
    67: ("rain", "大冻雨"),
    71: ("snow", "小雪"),
    73: ("snow", "中雪"),
    75: ("snow", "大雪"),
    77: ("snow", "米雪"),
    80: ("rain", "小阵雨"),
    81: ("rain", "阵雨"),
    82: ("rain", "大阵雨"),
    85: ("snow", "小阵雪"),
    86: ("snow", "大阵雪"),
    95: ("storm", "雷暴"),
    96: ("storm", "雷暴伴冰雹"),
    99: ("storm", "强雷暴伴冰雹"),
}

# ── Climate zone tables ────────────────────────────────────────────
# zone -> [jan, feb, ..., dec] temp_c
_CLIMATE_TEMP: Final[dict[str, list[float]]] = {
    "equator":      [27, 27, 27, 27, 27, 27, 27, 27, 27, 27, 27, 27],
    "subtropical":  [15, 16, 19, 23, 27, 30, 30, 29, 27, 23, 19, 15],
    "temperate":    [2,  3,  7, 12, 17, 21, 23, 22, 18, 12,  7,  3],
    "subarctic":   [-15,-13, -5,  3, 10, 15, 18, 16, 10,  2, -7,-13],
    "polar":       [-30,-32,-28,-20,-10, -2,  0, -2,-10,-20,-28,-30],
}


def _climate_zone(lat: float) -> str:
    """Map latitude to a climate zone name."""
    abs_lat = abs(lat)
    if abs_lat < 10:
        return "equator"
    if abs_lat < 30:
        return "subtropical"
    if abs_lat < 55:
        return "temperate"
    if abs_lat < 70:
        return "subarctic"
    return "polar"


def _stable_random(lat: float, lon: float, low: float, high: float) -> float:
    """Return a deterministic pseudo-random float seeded by lat/lon."""
    seed_str = f"{lat:.2f},{lon:.2f}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed).uniform(low, high)


def _climate_fallback(lat: float, lon: float, elevation: float | None = None,
                      local_hour: int | None = None) -> dict[str, Any]:
    """Offline climate-zone estimate. Always returns a valid dict."""
    zone = _climate_zone(lat)
    month = datetime.date.today().month - 1  # 0-indexed
    # Southern hemisphere: shift month by 6 to flip seasons
    if lat < 0:
        month = (month + 6) % 12
    temp = _CLIMATE_TEMP[zone][month]
    # Diurnal (day/night) temperature variation
    if local_hour is not None:
        # Peak at 14:00, trough at 05:00
        # Amplitude: ±8°C for lowlands, ±12°C for deserts
        amplitude = 12.0 if zone in ("equator", "subtropical") else 8.0
        hour_angle = (local_hour - 5) * (2 * math.pi / 24)  # trough at 5am
        temp += amplitude * math.sin(hour_angle)
    # Atmospheric lapse rate correction: ~6.5°C per 1000m
    if elevation and elevation > 0:
        temp -= elevation * 0.0065
    wind = _stable_random(lat, lon, 3.0, 8.0)
    return {
        "temp_c": round(temp, 1),
        "feels_c": round(temp - 2, 1),
        "wind_ms": round(wind, 1),
        "humidity": 60.0,
        "precip": "none",
        "text": "气候估算",
        "source": "climate",
    }


# ── QWeather ───────────────────────────────────────────────────────


def _precip_from_text(text: str) -> str:
    """Infer precipitation type from Chinese weather description."""
    for kw, p in [("雪", "snow"), ("雷", "storm"), ("雨", "rain"), ("冰雹", "storm")]:
        if kw in text:
            return p
    return "none"


async def _try_qweather(lat: float, lon: float) -> dict[str, Any] | None:
    """Try QWeather API. Returns dict on success, None on failure."""
    key = os.environ.get("NOWHERE_QWEATHER_KEY", "")
    if not key:
        return None
    url = f"https://devapi.qweather.com/v7/weather/now?location={lon},{lat}&key={key}"
    data = await providers.fetch_json(url, source="qweather", cache_ttl=300)
    if data is None:
        return None
    if str(data.get("code")) != "200":
        return None
    now = data.get("now")
    if not now:
        return None
    try:
        temp = float(now["temp"])
        feels = float(now["feelsLike"])
        wind = float(now["windSpeed"])
        humidity = float(now["humidity"])
        text = now.get("text", "")
    except (KeyError, ValueError, TypeError):
        return None
    return {
        "temp_c": temp,
        "feels_c": feels,
        "wind_ms": wind,
        "humidity": humidity,
        "precip": _precip_from_text(text),
        "text": text,
        "source": "qweather",
    }


# ── Open-Meteo ─────────────────────────────────────────────────────


async def _try_openmeteo(lat: float, lon: float) -> dict[str, Any] | None:
    """Try Open-Meteo free API. Returns dict on success, None on failure."""
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
        f"precipitation,weather_code,wind_speed_10m"
    )
    data = await providers.fetch_json(url, source="openmeteo", cache_ttl=300)
    if data is None:
        return None
    cur = data.get("current")
    if not cur:
        return None
    try:
        temp = float(cur["temperature_2m"])
        feels = float(cur["apparent_temperature"])
        humidity = float(cur["relative_humidity_2m"])
        wind = float(cur["wind_speed_10m"])
        code = int(cur["weather_code"])
        precip_val = float(cur.get("precipitation", 0))
    except (KeyError, ValueError, TypeError):
        return None
    # Look up WMO code
    precip_type, text = _WMO_MAP.get(code, ("none", "未知"))
    # If WMO says no precipitation but precipitation > 0, call it rain
    if precip_type == "none" and precip_val > 0:
        precip_type = "rain"
        text = "降水"
    return {
        "temp_c": temp,
        "feels_c": feels,
        "wind_ms": wind,
        "humidity": humidity,
        "precip": precip_type,
        "text": text,
        "source": "openmeteo",
    }


# ── Public API ─────────────────────────────────────────────────────


async def current(lat: float, lon: float, elevation: float | None = None,
                  local_hour: int | None = None) -> dict[str, Any]:
    """Return weather at (lat, lon).  Never None, never raises.

    Fallback chain: climate zone offline (fast) -> Open-Meteo (slow).
    Applies atmospheric lapse rate correction when elevation is provided.
    Applies diurnal temperature variation when local_hour is provided.
    """
    # 1) Climate zone offline (instant, no network)
    result = _climate_fallback(lat, lon, elevation=elevation, local_hour=local_hour)

    # 2) Try online for better accuracy (non-blocking, best-effort)
    try:
        online = await _try_openmeteo(lat, lon)
        if online is not None:
            result = _apply_corrections(online, elevation, local_hour, lat)
    except Exception:
        pass

    return result


def _apply_corrections(result: dict, elevation: float | None, local_hour: int | None,
                       lat: float) -> dict:
    """Apply lapse rate and diurnal corrections to a weather result."""
    # Lapse rate: -6.5°C per 1000m
    if elevation and elevation > 0:
        correction = elevation * 0.0065
        result["temp_c"] = round(result["temp_c"] - correction, 1)
        result["feels_c"] = round(result["feels_c"] - correction, 1)

    # Diurnal variation: peak at 14:00, trough at 05:00
    if local_hour is not None:
        zone = _climate_zone(lat)
        amplitude = 12.0 if zone in ("equator", "subtropical") else 8.0
        hour_angle = (local_hour - 5) * (2 * math.pi / 24)
        diurnal = amplitude * math.sin(hour_angle)
        result["temp_c"] = round(result["temp_c"] + diurnal, 1)
        result["feels_c"] = round(result["feels_c"] + diurnal, 1)

    return result
