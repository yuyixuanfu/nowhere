"""Tests for landing pool, walk physics, and WorldState."""

import random
from datetime import datetime, timezone
from unittest.mock import patch

from nowhere import landing, walk
from nowhere.state import WorldState


def test_pool_spot_has_biome():
    s = landing.random_spot(random.Random(42))
    assert s["biome"] in {
        "volcano", "coast", "rainforest", "desert",
        "tundra", "city", "island", "mountain",
    }


def test_pool_spot_has_coords():
    s = landing.random_spot(random.Random(0))
    assert isinstance(s["lat"], float)
    assert isinstance(s["lon"], float)
    assert "name_hint" in s


def test_pool_spot_jitter():
    """Two calls with different seeds should give different jittered coords."""
    s1 = landing.random_spot(random.Random(1))
    s2 = landing.random_spot(random.Random(2))
    # They might pick the same base spot, but jitter should differ
    # (with 64 spots and 2 seeds, extremely unlikely to collide exactly)
    assert s1["lat"] != s2["lat"] or s1["lon"] != s2["lon"]


def test_walk_moves_position():
    s = WorldState()
    s.pos = (35.3606, 138.7274)
    s.mode = "land"
    r = walk.step(s, bearing_deg=180.0, semantic=None, dist_km=2.0)
    assert r["blocked"] is False and s.pos[0] < 35.3606


def test_walk_uphill_gains_elevation():
    s = WorldState()
    s.pos = (27.5, 86.5)  # SW flank of Everest region
    s.mode = "land"
    r = walk.step(s, bearing_deg=None, semantic="uphill", dist_km=1.0)
    assert r["elevation_delta"] > 20


def test_walk_cliff_blocked():
    """Test cliff blocking via monkeypatch (grid_tiny is too coarse for real 45deg slopes)."""
    s = WorldState()
    s.pos = (27.9881, 86.9250)  # Everest summit
    s.mode = "land"
    orig_pos = s.pos

    # Monkeypatch slope_between to return a cliff-grade slope
    import nowhere.terrain as terrain_mod
    orig_slope = terrain_mod.slope_between

    def fake_slope_between(a, b):
        return (50.0, 0.3)  # 50 degrees, 0.3 km

    with patch.object(terrain_mod, "slope_between", side_effect=fake_slope_between):
        r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=0.3)

    assert r["blocked"] is True and s.pos == orig_pos and r["reason"] == "cliff"


def test_walk_into_sea_switches_mode():
    s = WorldState()
    s.pos = (35.60, 139.78)  # Tokyo Bay shore
    s.mode = "land"

    # Monkeypatch is_water to simulate ocean at destination
    import nowhere.terrain as terrain_mod
    orig_is_water = terrain_mod.is_water

    call_count = [0]

    def fake_is_water(lat, lon):
        # The starting point is land, but after moving SE we hit water
        call_count[0] += 1
        if call_count[0] <= 1:
            return False  # current position is land
        return True  # new position is water

    with patch.object(terrain_mod, "is_water", side_effect=fake_is_water):
        r = walk.step(s, bearing_deg=135.0, semantic=None, dist_km=3.0)

    assert s.mode == "water" and r["entered_water"] is True


def test_walk_comes_ashore():
    s = WorldState()
    s.pos = (35.60, 139.78)
    s.mode = "water"

    import nowhere.terrain as terrain_mod

    call_count = [0]

    def fake_is_water(lat, lon):
        call_count[0] += 1
        if call_count[0] <= 1:
            return True  # currently in water
        return False  # new position is land

    with patch.object(terrain_mod, "is_water", side_effect=fake_is_water):
        r = walk.step(s, bearing_deg=315.0, semantic=None, dist_km=3.0)

    assert s.mode == "land" and r["entered_water"] is False


def test_time_advances():
    s = WorldState()
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    s.pos = (35.42, 138.73)
    s.mode = "land"
    walk.step(s, bearing_deg=90.0, semantic=None, dist_km=2.0)
    assert s.elapsed_hours > 0.3


def test_time_water_slower():
    """Water speed (1.5 km/h) should take more time than land (4 km/h)."""
    import nowhere.terrain as terrain_mod

    s_land = WorldState()
    s_land.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    s_land.pos = (35.42, 138.73)
    s_land.mode = "land"

    s_water = WorldState()
    s_water.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    s_water.pos = (35.42, 138.73)
    s_water.mode = "water"

    walk.step(s_land, bearing_deg=90.0, semantic=None, dist_km=2.0)

    # Keep the water state in water mode by patching is_water
    with patch.object(terrain_mod, "is_water", return_value=True):
        walk.step(s_water, bearing_deg=90.0, semantic=None, dist_km=2.0)

    assert s_water.elapsed_hours > s_land.elapsed_hours


def test_dist_clamped():
    """dist_km should be clamped to [0.2, 5.0]."""
    s = WorldState()
    s.pos = (35.42, 138.73)
    s.mode = "land"
    r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=100.0)
    assert r["dist_km"] == 5.0

    r2 = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=0.01)
    assert r2["dist_km"] == 0.2


def test_path_appended():
    s = WorldState()
    s.pos = (35.42, 138.73)
    s.mode = "land"
    walk.step(s, bearing_deg=90.0, semantic=None, dist_km=1.0)
    assert len(s.path) == 1
    assert "lat" in s.path[0] and "lon" in s.path[0]


def test_world_state_now():
    s = WorldState()
    assert s.now() is None
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    s.elapsed_hours = 2.5
    expected = datetime(2026, 7, 20, 2, 30, tzinfo=timezone.utc)
    assert s.now() == expected


def test_slope_halves_speed():
    """On steep terrain (>20deg), speed should be halved."""
    s = WorldState()
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    s.pos = (35.42, 138.73)
    s.mode = "land"

    # Normal flat walk
    s_flat = WorldState()
    s_flat.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    s_flat.pos = (35.42, 138.73)
    s_flat.mode = "land"

    import nowhere.terrain as terrain_mod

    # Walk on flat terrain
    walk.step(s_flat, bearing_deg=90.0, semantic=None, dist_km=2.0)

    # Walk on steep terrain (monkeypatch slope to return >20deg)
    orig = terrain_mod.slope_between

    def steep_slope(a, b):
        return (30.0, 1.0)

    with patch.object(terrain_mod, "slope_between", side_effect=steep_slope):
        walk.step(s, bearing_deg=90.0, semantic=None, dist_km=2.0)

    # Steep terrain should take more time (speed halved)
    assert s.elapsed_hours > s_flat.elapsed_hours
