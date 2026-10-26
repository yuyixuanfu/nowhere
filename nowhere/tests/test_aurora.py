"""Tests for the NOAA OVATION aurora module and its integration points.

These run offline: the network layer is replaced with a fixture payload so
CI never depends on SWPC being reachable.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest

from nowhere import aurora, describe, sky


@pytest.fixture(autouse=True)

def _clear_cache():
    aurora.reset_cache()
    yield
    aurora.reset_cache()


def _fake_grid(**overrides):
    """Build a minimal OVATION-shaped payload."""
    coords = []
    for lat in range(-90, 91):
        for lon in range(360):
            value = 0
            # Put a strong aurora over Svalbard (78N, 15E) and nothing else.
            if lat == 78 and lon == 15:
                value = overrides.get("power", 8)
            coords.append([lon, lat, value])
    return {
        "Observation Time": "2026-10-26T05:49:00Z",
        "Forecast Time": "2026-10-26T07:12:00Z",
        "Data Format": "[Longitude, Latitude, Aurora]",
        "coordinates": coords,
    }


def test_forecast_reads_real_power(monkeypatch):
    payload = _fake_grid(power=8)
    monkeypatch.setattr(aurora, "_fetch_grid", lambda: {
        "grid": [[payload["coordinates"][lat * 360 + lon][2]
                  for lon in range(360)] for lat in range(181)],
        "observation_time": payload["Observation Time"],
        "forecast_time": payload["Forecast Time"],
    })
    result = aurora.forecast(78.2, 15.6)
    assert result is not None
    assert result["power"] == 8
    assert result["source"] == "noaa_ovation"
    # power 8 -> intensity 4, green_purple, curtain
    assert result["intensity"] == 4
    assert result["color"] == "green_purple"
    assert result["shape"] == "curtain"


def test_forecast_returns_none_over_tropics(monkeypatch):
    monkeypatch.setattr(aurora, "_fetch_grid", lambda: {
        "grid": [[9] * 360 for _ in range(181)],
        "observation_time": None,
        "forecast_time": None,
    })
    # Guangzhou: outside the auroral zone, must not invent an aurora.
    assert aurora.forecast(23.13, 113.26) is None


def test_forecast_returns_none_when_power_zero(monkeypatch):
    monkeypatch.setattr(aurora, "_fetch_grid", lambda: {
        "grid": [[0] * 360 for _ in range(181)],
        "observation_time": None,
        "forecast_time": None,
    })
    assert aurora.forecast(69.65, 18.96) is None


def test_forecast_survives_network_failure(monkeypatch):
    monkeypatch.setattr(aurora, "_fetch_grid", lambda: None)
    assert aurora.forecast(69.65, 18.96) is None


def test_visible_sky_falls_back_when_forecast_missing(monkeypatch):
    """Offline: sky.visible_sky must still work, using the latitude heuristic."""
    monkeypatch.setattr(aurora, "available", lambda: False)
    monkeypatch.setattr(aurora, "forecast", lambda lat, lon: None)
    now = datetime(2026, 10, 26, 22, 0, tzinfo=timezone.utc)
    payload = sky.visible_sky(69.65, 18.96, now, random.Random(1))
    assert "aurora" in payload  # key always present, may be None or heuristic
    assert "planets" in payload


def test_visible_sky_uses_real_forecast(monkeypatch):
    real = {"color": "green_purple", "shape": "curtain", "intensity": 4,
            "power": 8, "source": "noaa_ovation"}
    monkeypatch.setattr(aurora, "forecast", lambda lat, lon: real)
    now = datetime(2026, 10, 26, 22, 0, tzinfo=timezone.utc)
    payload = sky.visible_sky(78.2, 15.6, now, random.Random(1))
    assert payload["aurora"] == real


def test_aurora_sentence_survives_sanity_check():
    """Regression: "极光" was in the notable-places set, so sanity_check
    treated it as a mismatched place name and deleted the whole sentence."""
    text = ("天黑了。满月。木星挂在那里,26 度高。"
            "极光来了,绿里带紫,像帘子一样垂下来,动得急,整个天空都在抖。")
    env = {
        "weather": {"temp_c": 2, "wind_ms": 3.0, "precip": "none"},
        "sky": {"phase": "nautical"},
        "_season": "autumn",
        "_place": "朗伊尔城",
        "_cc": "SJ",
        "biome": "",
    }
    out = describe.sanity_check(text, env)
    assert "极光" in out, "aurora sentence must not be eaten as a place-name mismatch"


def test_sky_delta_notices_aurora():
    """Aurora appearing is a real change; _sky_delta must not return 0."""
    from nowhere import server

    old = {"phase": "night", "aurora": None}
    new = {"phase": "night", "aurora": {"power": 8}}
    assert server._sky_delta(old, new) > 0.5


def test_sky_delta_quiet_without_change():
    from nowhere import server

    same = {"phase": "night", "aurora": {"power": 1}}
    assert server._sky_delta(same, dict(same)) == 0.0
