"""Tests for soundscape (offline ambient sound descriptions)."""

from __future__ import annotations

import random

from nowhere import soundscape


def test_rain_dominates():
    env = {"weather": {"precip": "rain", "wind_ms": 3, "temp_c": 20}, "surface": "forest", "sky": {"phase": "day"}}
    s = soundscape.describe_sound(env, random.Random(1))
    assert "雨" in s


def test_snow_is_quiet():
    env = {"weather": {"precip": "snow", "wind_ms": 8, "temp_c": -5}, "surface": "snow", "sky": {"phase": "day"}}
    s = soundscape.describe_sound(env, random.Random(2))
    assert "雪" in s and "风在吼" not in s  # 雪天风声被压过


def test_strong_wind():
    env = {"weather": {"precip": "none", "wind_ms": 15, "temp_c": 10}, "surface": "rock", "sky": {"phase": "day"}}
    s = soundscape.describe_sound(env, random.Random(3))
    assert "风" in s


def test_forest_wind_detail():
    env = {"weather": {"precip": "none", "wind_ms": 8, "temp_c": 20}, "surface": "forest", "sky": {"phase": "day"}}
    s = soundscape.describe_sound(env, random.Random(4))
    assert "树叶子" in s


def test_night_insects():
    env = {"weather": {"precip": "none", "wind_ms": 1, "temp_c": 25}, "surface": "grass", "sky": {"phase": "night"}}
    s = soundscape.describe_sound(env, random.Random(5))
    assert "虫" in s


def test_dead_quiet():
    env = {"weather": {"precip": "none", "wind_ms": 0, "temp_c": 5}, "surface": "bare", "sky": {"phase": "day"}}
    s = soundscape.describe_sound(env, random.Random(6))
    assert len(s) > 4  # 静也是一种声景,不许空串


def test_reproducible():
    env = {"weather": {"precip": "none", "wind_ms": 8, "temp_c": 20}, "surface": "forest", "sky": {"phase": "day"}}
    a = soundscape.describe_sound(env, random.Random(7))
    b = soundscape.describe_sound(env, random.Random(7))
    assert a == b


def test_rain_on_forest_no_english_surface():
    """B4: rain template must not contain English surface key 'forest'."""
    env = {"weather": {"precip": "rain", "wind_ms": 3, "temp_c": 20}, "surface": "forest", "sky": {"phase": "day"}}
    # Run multiple seeds to hit all rain variants
    for seed in range(20):
        s = soundscape.describe_sound(env, random.Random(seed))
        assert "forest" not in s, f"English 'forest' leaked in output (seed={seed}): {s}"
