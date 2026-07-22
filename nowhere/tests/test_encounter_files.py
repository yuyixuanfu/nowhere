"""Tests for nowhere.encounters -- global encounter file pool."""

from __future__ import annotations

import random

from nowhere import encounters


def test_draw_encounter_arctic():
    """Polar latitudes draw from polar encounters."""
    rng = random.Random(42)
    result = encounters.draw_encounter("tundra", 75.0, 0.0, rng)
    assert result is not None
    assert len(result) > 10


def test_draw_encounter_antarctic():
    """Southern polar latitudes also draw from polar encounters."""
    rng = random.Random(42)
    result = encounters.draw_encounter("tundra", -70.0, 0.0, rng)
    assert result is not None
    assert len(result) > 10


def test_draw_encounter_africa():
    """African coordinates draw from Africa encounters."""
    rng = random.Random(42)
    result = encounters.draw_encounter("desert", 0.0, 20.0, rng)
    assert result is not None
    assert len(result) > 10


def test_draw_encounter_asia():
    """Asian coordinates draw from Asia encounters."""
    rng = random.Random(42)
    result = encounters.draw_encounter("mountain", 30.0, 100.0, rng)
    assert result is not None
    assert len(result) > 10


def test_draw_encounter_americas():
    """Americas coordinates draw from Americas encounters."""
    rng = random.Random(42)
    result = encounters.draw_encounter("forest", 40.0, -100.0, rng)
    assert result is not None
    assert len(result) > 10


def test_draw_encounter_oceania():
    """Oceanian coordinates draw from Americas+Oceania encounters."""
    rng = random.Random(42)
    result = encounters.draw_encounter("coast", -25.0, 150.0, rng)
    assert result is not None
    assert len(result) > 10


def test_draw_encounter_europe():
    """European coordinates draw from humans encounters."""
    rng = random.Random(42)
    result = encounters.draw_encounter("city", 48.0, 2.0, rng)
    assert result is not None
    assert len(result) > 10


def test_draw_encounter_default():
    """Coordinates outside known regions fall back to humans encounters."""
    rng = random.Random(42)
    # A point in the mid-Atlantic that doesn't match any region
    result = encounters.draw_encounter("", 0.0, -25.0, rng)
    assert result is not None
    assert len(result) > 10


def test_draw_encounter_variety():
    """Multiple calls with different seeds produce different results."""
    results = set()
    for seed in range(20):
        rng = random.Random(seed)
        r = encounters.draw_encounter("tundra", 75.0, 0.0, rng)
        if r:
            results.add(r)
    # With 38 polar encounters and 20 draws, we should get several distinct ones
    assert len(results) >= 5


def test_draw_encounter_biome_ignored_for_region():
    """Biome string does not override geographic region selection."""
    rng = random.Random(42)
    # Arctic with "desert" biome still uses polar file
    result = encounters.draw_encounter("desert", 75.0, 0.0, rng)
    assert result is not None
