"""Tests for salience ranking and describe rendering."""

from __future__ import annotations

import inspect
import random

from nowhere import salience
import nowhere.describe as d


# ── helpers ──────────────────────────────────────────────────────────


def _c(kind: str, delta: float, novelty: float, bd: float, **payload) -> dict:
    """Build a candidate dict for salience.rank."""
    return {
        "kind": kind,
        "delta": delta,
        "novelty": novelty,
        "body_distance": bd,
        "payload": payload,
    }


# ── salience tests ───────────────────────────────────────────────────


def test_rank_top3():
    cands = [
        _c("weather", 0.9, 0.2, 0.1, temp_c=4),
        _c("art", 0.0, 0.8, 0.9),
        _c("life", 0.0, 0.9, 0.5),
        _c("sky", 0.1, 0.3, 0.8),
        _c("terrain", 0.5, 0.2, 0.0, slope_deg=18),
        _c("radio", 0.0, 0.3, 0.7),
    ]
    top = salience.rank(cands, random.Random(1))
    assert len(top) == 3
    assert top[0]["kind"] == "weather"


def test_rank_empty():
    assert salience.rank([], random.Random(1)) == []


# ── describe.render tests ────────────────────────────────────────────


def test_render_weather_delta():
    s = d.render(
        "weather",
        {"temp_c": 4, "feels_c": -2, "wind_ms": 11, "text": "多云", "precip": "none"},
        {"weather": {"temp_c": 13, "wind_ms": 3}},
        random.Random(2),
    )
    assert "9" in s  # mentions the 9-degree change


def test_render_reproducible():
    payload = {
        "surface": "rock",
        "slope_deg": 18,
        "elevation": 2240,
        "elevation_delta": 310,
    }
    a = d.render("terrain", payload, None, random.Random(7))
    b = d.render("terrain", payload, None, random.Random(7))
    assert a == b


def test_no_empty_adjectives():
    src = inspect.getsource(d)
    for bad in ["很", "非常", "十分", "仙境", "震撼", "绝美", "梦幻", "无法形容", "令人窒息"]:
        assert bad not in src, f"Forbidden word '{bad}' found in describe.py"


def test_render_life():
    s = d.render(
        "life",
        {"common_name": "小熊猫", "distance_m": 200, "seen_at": "2026-07-17"},
        None,
        random.Random(3),
    )
    assert "小熊猫" in s
    assert "200" in s


def test_compose_joins():
    s = d.compose(["你落在富士山。", "风 11 m/s。"], random.Random(1))
    assert "富士山" in s
    assert "11 m/s" in s


def test_compose_empty():
    assert d.compose([], random.Random(1)) == ""


def test_render_arrive():
    s = d.render("arrive", {"place": "富士山", "时段": "白天"}, None, random.Random(0))
    assert "富士山" in s


def test_render_terrain():
    s = d.render(
        "terrain",
        {"surface": "rock", "slope_deg": 18, "elevation": 2240, "elevation_delta": 310},
        None,
        random.Random(5),
    )
    assert "2240" in s
    assert "310" in s


def test_render_sky_night():
    s = d.render(
        "sky",
        {
            "phase": "night",
            "sun_alt": -30,
            "moon_phase": 0.95,
            "moon_alt": 45,
            "planets": [{"name": "Jupiter", "alt": 40, "mag": -2.0}],
            "milky_way_core_up": True,
        },
        None,
        random.Random(6),
    )
    assert "木星" in s or "满月" in s


def test_render_water():
    s = d.render("water", {"sea_surface_temp": 19}, None, random.Random(4))
    assert "19" in s


def test_render_art():
    s = d.render(
        "art",
        {"title": "星夜", "artist": "梵高", "why": "与夜空呼应"},
        None,
        random.Random(8),
    )
    assert "星夜" in s


def test_render_radio():
    s = d.render("radio", {"name": "FM88.7", "genre": "爵士"}, None, random.Random(9))
    assert "FM88.7" in s


def test_render_blocked():
    s = d.render("blocked", {"reason": "60° 的岩壁"}, None, random.Random(10))
    assert "岩壁" in s or "不通" in s


def test_render_message():
    s = d.render("message", {"content": "小心落石"}, None, random.Random(11))
    assert "小心落石" in s


def test_render_unknown_kind():
    s = d.render("unknown", {}, None, random.Random(0))
    assert s == ""


# ── B3: compose should skip empty/whitespace-only sections ────────────


def test_compose_skips_empty_sections():
    """compose(['', '风 3 米每秒。', '']) must NOT start with '走着走着'."""
    rng = random.Random(42)
    s = d.compose(["", "风 3 米每秒。", ""], rng)
    assert not s.startswith("走着走着"), f"Got: {s!r}"
    assert "风 3 米每秒" in s


# ── B5: _time_of_day(12) should return "正午" ─────────────────────────


def test_time_of_day_noon():
    assert d._time_of_day(12, "day") == "正午"


# ── B6: high-elevation flat should NOT say "平川" ──────────────────────


def test_terrain_high_flat_no_pingchuan():
    """At 4686m with slope < 1°, output must not contain '平川'."""
    s = d.render(
        "terrain",
        {"surface": "rock", "slope_deg": 0.5, "elevation": 4686},
        None,
        random.Random(99),
    )
    assert "平川" not in s, f"High-alt flat got '平川': {s!r}"
    assert "4686" in s


def test_terrain_urban_flat_no_pingchuan():
    """Urban flat surface must not use '一马平川'."""
    s = d.render(
        "terrain",
        {"surface": "urban", "slope_deg": 0.3, "elevation": 100},
        None,
        random.Random(42),
    )
    assert "一马平川" not in s, f"Urban flat got '一马平川': {s!r}"
    assert "硬化路面" in s or "马路" in s or "人行道" in s


def test_terrain_rock_flat_no_pingchuan():
    """Rock flat surface at moderate elevation must not use '一马平川'."""
    s = d.render(
        "terrain",
        {"surface": "rock", "slope_deg": 0.2, "elevation": 500},
        None,
        random.Random(77),
    )
    assert "一马平川" not in s, f"Rock flat got '一马平川': {s!r}"


def test_all_kinds_have_variants():
    """Every registered kind must have >= 3 variants."""
    pool_map = {
        "arrive": d._ARRIVE_VARIANTS,
        "weather_abs": d._WEATHER_ABS_VARIANTS,
        "weather_delta": d._WEATHER_DELTA_VARIANTS,
        "terrain": d._TERRAIN_VARIANTS,
        "terrain_flat": d._TERRAIN_FLAT_VARIANTS,
        "terrain_flat_bare": d._TERRAIN_FLAT_BARE_VARIANTS,
        "terrain_flat_rock": d._TERRAIN_FLAT_ROCK_VARIANTS,
        "terrain_flat_urban": d._TERRAIN_FLAT_URBAN_VARIANTS,
        "terrain_high_flat": d._TERRAIN_HIGH_FLAT_VARIANTS,
        "terrain_scree": d._TERRAIN_SCREE_VARIANTS,
        "sky_night": d._SKY_NIGHT_VARIANTS,
        "sky_day": d._SKY_DAY_VARIANTS,
        "water": d._WATER_VARIANTS,
        "life": d._LIFE_VARIANTS,
        "art": d._ART_VARIANTS,
        "radio": d._RADIO_VARIANTS,
        "blocked": d._BLOCKED_VARIANTS,
        "message": d._MESSAGE_VARIANTS,
    }
    for name, pool in pool_map.items():
        assert len(pool) >= 3, f"{name} has only {len(pool)} variants"
