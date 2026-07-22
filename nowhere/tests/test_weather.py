"""Tests for weather.current() and water.sea_surface_temp()."""

from __future__ import annotations

import httpx
import pytest

from nowhere import weather, water


# ── weather.current ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_qweather_path(respx_mock, monkeypatch):
    monkeypatch.setenv("NOWHERE_QWEATHER_KEY", "k")
    respx_mock.get(url__startswith="https://devapi.qweather.com").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": "200",
                "now": {
                    "temp": "26",
                    "feelsLike": "28",
                    "windSpeed": "12",
                    "humidity": "80",
                    "text": "小雨",
                },
            },
        )
    )
    r = await weather.current(22.3, 114.2)
    assert r["source"] == "qweather"
    assert r["temp_c"] == 26.0
    assert r["feels_c"] == 28.0
    assert r["wind_ms"] == 12.0
    assert r["humidity"] == 80.0
    assert r["precip"] == "rain"
    assert r["text"] == "小雨"


@pytest.mark.anyio
async def test_openmeteo_fallback(respx_mock, monkeypatch):
    monkeypatch.delenv("NOWHERE_QWEATHER_KEY", raising=False)
    respx_mock.get(url__startswith="https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(
            200,
            json={
                "current": {
                    "temperature_2m": 18.0,
                    "apparent_temperature": 16.0,
                    "relative_humidity_2m": 60,
                    "precipitation": 0.0,
                    "weather_code": 2,
                    "wind_speed_10m": 14.0,
                },
            },
        )
    )
    r = await weather.current(48.85, 2.35)
    assert r["source"] == "openmeteo"
    assert r["temp_c"] == 18.0
    assert r["feels_c"] == 16.0
    assert r["wind_ms"] == 14.0
    assert r["humidity"] == 60.0
    assert r["precip"] == "none"
    # WMO code 2 = "多云" (cloudy)
    assert "云" in r["text"] or "晴" in r["text"] or "阴" in r["text"]


@pytest.mark.anyio
async def test_openmeteo_rain_code(respx_mock, monkeypatch):
    monkeypatch.delenv("NOWHERE_QWEATHER_KEY", raising=False)
    respx_mock.get(url__startswith="https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(
            200,
            json={
                "current": {
                    "temperature_2m": 10.0,
                    "apparent_temperature": 8.0,
                    "relative_humidity_2m": 90,
                    "precipitation": 2.5,
                    "weather_code": 61,
                    "wind_speed_10m": 5.0,
                },
            },
        )
    )
    r = await weather.current(51.5, -0.1)
    assert r["source"] == "openmeteo"
    assert r["precip"] == "rain"


@pytest.mark.anyio
async def test_openmeteo_snow_code(respx_mock, monkeypatch):
    monkeypatch.delenv("NOWHERE_QWEATHER_KEY", raising=False)
    respx_mock.get(url__startswith="https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(
            200,
            json={
                "current": {
                    "temperature_2m": -5.0,
                    "apparent_temperature": -10.0,
                    "relative_humidity_2m": 80,
                    "precipitation": 1.0,
                    "weather_code": 71,
                    "wind_speed_10m": 3.0,
                },
            },
        )
    )
    r = await weather.current(64.0, -21.0)
    assert r["source"] == "openmeteo"
    assert r["precip"] == "snow"


@pytest.mark.anyio
async def test_climate_last_resort(respx_mock):
    """When all HTTP fails, climate-zone fallback kicks in."""
    respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
    r1 = await weather.current(-1.0, 36.0)  # equator
    r2 = await weather.current(-80.0, 0.0)  # near south pole
    assert r1["source"] == "climate"
    assert r1["temp_c"] > 20
    assert r2["source"] == "climate"
    assert r2["temp_c"] < -20


@pytest.mark.anyio
async def test_never_returns_none():
    """weather.current must always return a dict, never None."""
    r = await weather.current(0.0, 0.0)
    assert isinstance(r, dict)
    assert "temp_c" in r
    assert "source" in r


@pytest.mark.anyio
async def test_never_raises(monkeypatch, respx_mock):
    """weather.current must never raise, even on total failure."""
    monkeypatch.delenv("NOWHERE_QWEATHER_KEY", raising=False)
    respx_mock.route().mock(side_effect=Exception("boom"))
    r = await weather.current(99.0, 999.0)
    assert isinstance(r, dict)
    assert r["source"] == "climate"


# ── water.sea_surface_temp ─────────────────────────────────────────


@pytest.mark.anyio
async def test_sst_land_is_none():
    """Beijing is land -> returns None."""
    assert await water.sea_surface_temp(39.9, 116.4) is None


@pytest.mark.anyio
async def test_sst_openmeteo_marine(respx_mock, monkeypatch):
    """Ocean point with working marine API."""
    # Force terrain.is_water to return True for this test
    monkeypatch.setattr("nowhere.terrain.is_water", lambda lat, lon: True)
    respx_mock.get(url__startswith="https://marine-api.open-meteo.com/v1/marine").mock(
        return_value=httpx.Response(
            200,
            json={"current": {"sea_surface_temperature": 22.5}},
        )
    )
    result = await water.sea_surface_temp(0.0, -30.0)
    assert result == 22.5


@pytest.mark.anyio
async def test_sst_climate_fallback(respx_mock, monkeypatch):
    """When marine API fails, climate fallback returns a float."""
    monkeypatch.setattr("nowhere.terrain.is_water", lambda lat, lon: True)
    respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
    result = await water.sea_surface_temp(0.0, -30.0)  # equator ocean
    assert result is not None
    assert result > 20  # equatorial water should be warm


@pytest.mark.anyio
async def test_sst_polar_climate(respx_mock, monkeypatch):
    """Polar ocean point with climate fallback."""
    monkeypatch.setattr("nowhere.terrain.is_water", lambda lat, lon: True)
    respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
    result = await water.sea_surface_temp(80.0, 0.0)  # near north pole
    assert result is not None
    assert result < 10
