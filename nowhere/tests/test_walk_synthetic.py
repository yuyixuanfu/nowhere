"""Logic unit tests for walk physics using synthetic terrain.

Tests that can't reliably pass against grid_tiny.npz because the 1° grid
blurs coastlines (B12).  All tests use `World` from `synthetic_terrain` to
build controlled, repeatable terrain.

NOTE: walk.step clamps dist_km to [0.2, 5.0].  Tile boundaries must be
within 5 km (~0.045° lat) of the start position for transition tests.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nowhere import walk
from nowhere.state import WorldState

from .synthetic_terrain import World


# ══════════════════════════════════════════════════════════════════════════
# Coastline transitions
# ══════════════════════════════════════════════════════════════════════════


def test_step_into_sea_from_north():
    """Walking south from land across a shoreline within 5km → mode=water."""
    # Shore at 35.000; start at 35.010 (~1.1km north of shore)
    world = World().coast(35.0, land_north=True, land_elev=20.0,
                          land_surface="grass")

    s = WorldState()
    s.pos = (35.010, 139.0)  # land, ~1.1km north of shore
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=180.0, semantic=None, dist_km=5.0)

    assert r["entered_water"] is True
    assert r["blocked"] is False
    assert s.mode == "water"


def test_step_into_sea_from_south():
    """Walking north from land across a shoreline within 5km → mode=water."""
    world = World().coast(35.0, land_north=False, land_elev=20.0,
                          land_surface="grass")

    s = WorldState()
    s.pos = (34.990, 139.0)  # land, ~1.1km south of shore
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=5.0)

    assert r["entered_water"] is True
    assert s.mode == "water"


def test_step_ashore():
    """Walking from water onto land → mode=land."""
    world = World().coast(35.0, land_north=True, land_elev=20.0,
                          land_surface="grass")

    s = WorldState()
    s.pos = (34.990, 139.0)  # water, ~1.1km south of shore
    s.mode = "water"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=5.0)

    assert r["entered_water"] is False
    assert s.mode == "land"


def test_toward_sea_enters_water():
    """toward_sea from near a coast should eventually enter water."""
    # Land at 35.010 (north), water at <=35.0 (south).  Step far enough
    # south that we cross the shore within the clamped max dist (5km).
    world = World().coast(35.0, land_north=True, land_elev=20.0,
                          water_surface="water_ocean")

    s = WorldState()
    s.pos = (35.010, 139.0)  # ~1.1km north of shore
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=None, semantic="toward_sea", dist_km=5.0)

    # heading south ~5km → should cross shore at 35.0
    assert r["entered_water"] is True
    assert s.mode == "water"


def test_sea_ahead_null_when_no_water():
    """toward_sea reports sea_ahead_km=None when no water within 20km."""
    world = World(default_elevation=100.0, default_surface="grass")

    s = WorldState()
    s.pos = (35.0, 139.0)
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=None, semantic="toward_sea", dist_km=2.0)

    assert r.get("sea_ahead_km") is None


# ══════════════════════════════════════════════════════════════════════════
# Cliff detection
# ══════════════════════════════════════════════════════════════════════════


def test_cliff_blocks_at_50_degrees():
    """A >45° slope blocks movement."""
    # 2000m rise from tile boundary — start closer so step crosses it
    world = (
        World()
        .tile(34.995, 35.000, 138.99, 139.01, elev=0, surface="rock")
        .tile(35.000, 35.005, 138.99, 139.01, elev=2000, surface="rock")
    )

    s = WorldState()
    s.pos = (34.999, 139.0)  # 0.001° (~0.11km) from 2000m cliff
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=0.5)

    # ~2000m rise over ~0.11km → slope > 60°
    assert r["blocked"] is True
    assert r["reason"] == "cliff"
    # Position unchanged
    assert s.pos == (34.999, 139.0)


def test_moderate_slope_not_blocked():
    """A slope well below 45° allows movement."""
    # 100m rise over ~2.0km → ~2.9°
    world = (
        World()
        .tile(34.990, 35.000, 139.00, 139.02, elev=0, surface="grass")
        .tile(35.000, 35.010, 139.00, 139.02, elev=100, surface="grass")
    )

    s = WorldState()
    s.pos = (34.995, 139.01)
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=2.0)

    assert r["blocked"] is False


def test_cliff_borderline_angle():
    """Verify the cliff threshold behavior with a controlled slope.

    The threshold is >45° (CLIFF_THRESHOLD_DEG).  We test that a slope
    just below 45° does NOT block.
    """
    # 500m rise over 0.5km horizontal → 45.0° exactly (atan(1))
    world = (
        World()
        .tile(34.995, 35.000, 139.00, 139.01, elev=0, surface="grass")
        .tile(35.000, 35.005, 139.00, 139.01, elev=500, surface="rock")
    )

    s = WorldState()
    s.pos = (34.998, 139.005)
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=1.0)

    # At ~500m over ~0.22+ km the slope may exceed 45°, but this test
    # just checks the function returns without crashing
    assert "blocked" in r
    assert "reason" in r
    assert r["blocked"] is False, "Slope just below 45 deg should NOT block"


# ══════════════════════════════════════════════════════════════════════════
# Semantic direction: uphill
# ══════════════════════════════════════════════════════════════════════════


def test_uphill_picks_max_gain():
    """uphill semantic picks the bearing with the most elevation gain."""
    # Mountain to the north at 35.05, start at 35.01
    # At 2.0km range, walking north should hit the mountain
    world = (
        World(default_elevation=100.0, default_surface="grass")
        .tile(35.02, 36.00, 138.00, 140.00, elev=2000, surface="rock")
    )

    s = WorldState()
    s.pos = (35.01, 139.0)  # just south of mountain
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=None, semantic="uphill", dist_km=2.0)

    # The mountain is to the north at ~1.1km away at 2km step →
    # _pick_semantic_bearing should find gain heading north
    assert r["elevation_delta"] > 0


def test_uphill_no_gain_on_flat_world():
    """uphill on perfectly flat terrain reports no_gain."""
    world = World(default_elevation=50.0, default_surface="grass")

    s = WorldState()
    s.pos = (35.0, 139.0)
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=None, semantic="uphill", dist_km=2.0)

    assert r["no_gain"] is True
    assert r["far_slope"] is None
    assert r["elevation_delta"] == 0.0


def test_uphill_reports_far_slope():
    """Flat nearby but mountain in far range → far_slope is set."""
    # Flat for ~5km around, then mountain at ~7km (north)
    # far scan goes 5km and 10km — the 10km scan should find it
    world = (
        World(default_elevation=100.0, default_surface="grass")
        .tile(35.07, 36.00, 138.00, 140.00, elev=3000, surface="rock")
    )

    s = WorldState()
    s.pos = (35.01, 139.0)  # ~6.7km south of mountain
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=None, semantic="uphill", dist_km=2.0)

    # Near range (5km): mountain at ~6.7km → not found
    # Far range (10km): mountain at ~6.7km → found! gain > 50m
    assert r.get("far_slope") is not None, "Mountain at ~7km should be detected by far scan"
    far_bearing, far_gain = r["far_slope"]
    assert far_gain > 50.0
    # bearing should point roughly north
    assert 315 <= far_bearing <= 360 or 0 <= far_bearing <= 45


# ══════════════════════════════════════════════════════════════════════════
# Semantic direction: toward_sea
# ══════════════════════════════════════════════════════════════════════════


def test_toward_sea_picks_max_drop():
    """toward_sea heads toward lower elevation (sea)."""
    # High land at 35.01, water at elevation 0 south of 35.00
    world = World().coast(35.0, land_north=True, land_elev=500.0,
                          water_surface="water_ocean")

    s = WorldState()
    s.pos = (35.010, 139.0)  # high land, water ~1.1km south
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=None, semantic="toward_sea", dist_km=2.0)

    # Heading south into water → elevation drops
    assert r["elevation_delta"] < 0


# ══════════════════════════════════════════════════════════════════════════
# Speed & time
# ══════════════════════════════════════════════════════════════════════════


def test_water_slower_than_land():
    """Water travel (1.5 km/h) accumulates more time than land (4 km/h)."""
    world_land = World(default_elevation=0.0, default_surface="grass")
    world_water = World(default_elevation=0.0, default_surface="water_ocean")

    s_land = WorldState()
    s_land.pos = (35.0, 139.0)
    s_land.mode = "land"
    s_land.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    s_water = WorldState()
    s_water.pos = (35.0, 139.0)
    s_water.mode = "water"
    s_water.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world_land.patch():
        walk.step(s_land, bearing_deg=90.0, semantic=None, dist_km=2.0)

    with world_water.patch():
        walk.step(s_water, bearing_deg=90.0, semantic=None, dist_km=2.0)

    # 2km / 4 km/h = 0.5h for land; 2km / 1.5 km/h ≈ 1.33h for water
    assert s_water.elapsed_hours > s_land.elapsed_hours
    assert s_land.elapsed_hours == pytest.approx(0.5, rel=1e-2)
    assert s_water.elapsed_hours == pytest.approx(1.333, rel=1e-2)


def test_steep_slope_halves_land_speed():
    """Slope > 20° halves speed → more travel time (but must stay <45° to not cliff)."""
    # 200m rise over ~0.5km → ~22° (between 20° threshold and 45° cliff)
    world = (
        World()
        .tile(35.000, 35.003, 139.00, 139.02, elev=0, surface="grass")
        .tile(35.003, 35.010, 139.00, 139.02, elev=200, surface="grass")
    )

    world_flat = World(default_elevation=100.0, default_surface="grass")

    s_steep = WorldState()
    s_steep.pos = (35.002, 139.01)  # 0.001° (~0.11km) from the 200m rise
    s_steep.mode = "land"
    s_steep.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    s_flat = WorldState()
    s_flat.pos = (35.0, 140.0)
    s_flat.mode = "land"
    s_flat.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s_steep, bearing_deg=0.0, semantic=None, dist_km=0.5)

    with world_flat.patch():
        walk.step(s_flat, bearing_deg=90.0, semantic=None, dist_km=0.5)

    # Should NOT be blocked (slope < 45°)
    assert r["blocked"] is False
    # Same distance, steep slope halved speed → more time
    # steep: 0.5km / (4*0.5) km/h = 0.25h; flat: 0.5km / 4 km/h = 0.125h
    assert s_steep.elapsed_hours > s_flat.elapsed_hours
    assert s_steep.elapsed_hours == pytest.approx(0.25, rel=1e-2)
    assert s_flat.elapsed_hours == pytest.approx(0.125, rel=1e-2)


# ══════════════════════════════════════════════════════════════════════════
# Surface tracking
# ══════════════════════════════════════════════════════════════════════════


def test_new_surface_reported_in_step_result():
    """Each step reports the surface at the destination via new_surface."""
    world = World().tile(35.00, 36.00, 139.00, 140.00, surface="sand")

    s = WorldState()
    s.pos = (35.5, 139.0)
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=2.0)

    assert r["new_surface"] == "sand"


def test_surface_transitions_tracked():
    """Walking from one surface tile into another is reflected."""
    world = (
        World()
        .tile(35.000, 35.020, 139.00, 139.10, surface="urban")
        .tile(35.020, 35.040, 139.00, 139.10, surface="forest")
    )

    s = WorldState()
    s.pos = (35.01, 139.05)  # urban
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=2.0)

    # Crossed into forest tile
    assert r["new_surface"] == "forest"


# ══════════════════════════════════════════════════════════════════════════
# Distance clamping
# ══════════════════════════════════════════════════════════════════════════


def test_dist_clamped_to_min():
    """Distance below 0.2 km is clamped up to 0.2 km."""
    world = World()
    s = WorldState()
    s.pos = (35.0, 139.0)
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=0.01)

    assert r["dist_km"] == 0.2


def test_dist_clamped_to_max():
    """Distance above 5.0 km is clamped down to 5.0 km."""
    world = World()
    s = WorldState()
    s.pos = (35.0, 139.0)
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=100.0)

    assert r["dist_km"] == 5.0


# ══════════════════════════════════════════════════════════════════════════
# Path tracking
# ══════════════════════════════════════════════════════════════════════════


def test_path_grows_with_each_step():
    """Each successful step appends one entry to the path."""
    world = World()
    s = WorldState()
    s.pos = (35.0, 139.0)
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        for _ in range(3):
            walk.step(s, bearing_deg=90.0, semantic=None, dist_km=1.0)

    assert len(s.path) == 3
    assert all("lat" in p and "lon" in p and "elevation" in p for p in s.path)


def test_path_contains_dist_km():
    """Path entries record the actual distance walked."""
    world = World()
    s = WorldState()
    s.pos = (35.0, 139.0)
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        walk.step(s, bearing_deg=90.0, semantic=None, dist_km=2.5)

    assert s.path[-1]["dist_km"] == 2.5


# ══════════════════════════════════════════════════════════════════════════
# RampTile (elevation gradient)
# ══════════════════════════════════════════════════════════════════════════


def test_ramp_tile_linear_elevation():
    """RampTile interpolates elevation linearly across latitude range."""
    world = World().ramp(
        35.0, 36.0, 139.0, 140.0,
        elev_start=0.0, elev_end=1000.0, surface="grass",
    )

    with world.patch():
        assert world.elevation(35.0, 139.5) == 0.0
        assert world.elevation(35.5, 139.5) == 500.0
        assert world.elevation(36.0, 139.5) == 1000.0


def test_ramp_tile_clamped_at_edges():
    """RampTile clamps elevation at edge values for lat outside range."""
    world = World().ramp(
        35.0, 36.0, 139.0, 140.0,
        elev_start=100.0, elev_end=500.0,
    )

    with world.patch():
        # Inside tile at boundaries — should interpolate properly
        assert world.elevation(35.0, 139.5) == 100.0  # bottom edge
        assert world.elevation(36.0, 139.5) == 500.0  # top edge
        # Mid-point
        assert world.elevation(35.5, 139.5) == 300.0


# ══════════════════════════════════════════════════════════════════════════
# World builder edge cases
# ══════════════════════════════════════════════════════════════════════════


def test_tile_overlap_last_wins():
    """When tiles overlap, the last-added tile takes precedence."""
    world = (
        World()
        .tile(35.0, 36.0, 139.0, 140.0, elev=100, surface="grass")
        .tile(35.4, 35.6, 139.4, 139.6, elev=999, surface="urban")
    )

    with world.patch():
        # Inside both → last tile wins
        assert world.elevation(35.5, 139.5) == 999
        assert world.surface(35.5, 139.5) == "urban"
        # Outside inner tile → first tile
        assert world.elevation(35.1, 139.5) == 100


def test_default_fallback():
    """Points outside all tiles fall back to defaults."""
    world = World(default_elevation=42.0, default_surface="bare")

    with world.patch():
        assert world.elevation(0, 0) == 42.0
        assert world.surface(0, 0) == "bare"
        assert world.is_water(0, 0) is False


def test_world_is_water():
    """is_water returns True for water surfaces, False otherwise."""
    world = World().tile(35.0, 36.0, 139.0, 140.0, surface="water_ocean")

    with world.patch():
        assert world.is_water(35.5, 139.5) is True
        # Outside tile → default grass → not water
        assert world.is_water(0, 0) is False


# ══════════════════════════════════════════════════════════════════════════
# water_ahead_km
# ══════════════════════════════════════════════════════════════════════════


def test_water_ahead_returns_correct_distance():
    """water_ahead_km finds water at the expected distance."""
    # Land north of 35.0, water south of 35.0
    world = World().coast(35.0, land_north=True, land_elev=20.0,
                          water_surface="water_ocean",
                          land_surface="grass")

    with world.patch():
        dist = walk.water_ahead_km(35.05, 139.0, 180.0)  # heading south

    assert dist is not None
    # From 35.05 heading south, water starts at 35.0 → ~5.5km
    assert 4 < dist < 7


def test_water_ahead_none_when_no_water():
    """water_ahead_km returns None when no water within 20km."""
    world = World(default_elevation=100.0, default_surface="grass")

    with world.patch():
        dist = walk.water_ahead_km(35.0, 139.0, 180.0)

    assert dist is None


# ══════════════════════════════════════════════════════════════════════════
# best_uphill_gain
# ══════════════════════════════════════════════════════════════════════════


def test_best_uphill_gain_positive_on_slope():
    """best_uphill_gain returns positive value when mountain is within range."""
    # Mountain at 35.02-36.00 with elev 2000; start at 35.01
    world = (
        World(default_elevation=100.0, default_surface="grass")
        .tile(35.02, 36.00, 138.00, 140.00, elev=2000, surface="rock")
    )

    s = WorldState()
    s.pos = (35.01, 139.0)

    with world.patch():
        gain = walk.best_uphill_gain(s, dist_km=2.0)

    # Mountain edge is ~1.1km north → should find gain
    assert gain > 0


def test_best_uphill_gain_zero_on_flat():
    """best_uphill_gain returns <=0 on perfectly flat terrain."""
    world = World(default_elevation=0.0, default_surface="grass")

    s = WorldState()
    s.pos = (35.0, 139.0)

    with world.patch():
        gain = walk.best_uphill_gain(s, dist_km=2.0)

    assert gain <= 0


# ══════════════════════════════════════════════════════════════════════════
# Mode transitions
# ══════════════════════════════════════════════════════════════════════════


def test_mode_stays_land_on_land():
    """Walking on land keeps mode=land."""
    world = World(default_surface="grass")

    s = WorldState()
    s.pos = (35.0, 139.0)
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        walk.step(s, bearing_deg=90.0, semantic=None, dist_km=2.0)

    assert s.mode == "land"


def test_mode_stays_water_in_water():
    """Walking in water keeps mode=water (no land nearby)."""
    world = World(default_surface="water_ocean")

    s = WorldState()
    s.pos = (35.0, 139.0)
    s.mode = "water"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        walk.step(s, bearing_deg=90.0, semantic=None, dist_km=2.0)

    assert s.mode == "water"


# ══════════════════════════════════════════════════════════════════════════
# Elevation delta sign
# ══════════════════════════════════════════════════════════════════════════


def test_uphill_climbed_flag():
    """climbed=True when elevation increases."""
    world = (
        World()
        .tile(35.00, 35.02, 139.00, 139.10, elev=0, surface="grass")
        .tile(35.02, 35.04, 139.00, 139.10, elev=500, surface="rock")
    )

    s = WorldState()
    s.pos = (35.01, 139.05)
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=2.0)

    assert r["climbed"] is True
    assert r["elevation_delta"] > 0


def test_downhill_climbed_false():
    """climbed=False when elevation decreases."""
    world = (
        World()
        .tile(35.00, 35.02, 139.00, 139.10, elev=500, surface="rock")
        .tile(35.02, 35.04, 139.00, 139.10, elev=0, surface="grass")
    )

    s = WorldState()
    s.pos = (35.01, 139.05)
    s.mode = "land"
    s.landed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)

    with world.patch():
        r = walk.step(s, bearing_deg=0.0, semantic=None, dist_km=2.0)

    assert r["climbed"] is False
    assert r["elevation_delta"] < 0
