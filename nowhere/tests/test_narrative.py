"""Unit tests for the walk narrative continuity system.

Tests _bearing_to_label, _build_walk_narrative, and narrative state tracking
without requiring terrain tiles or async calls.
"""

from __future__ import annotations

import random

from nowhere import server
from nowhere.state import WorldState


def _reset_state(surface: str = "grass"):
    """Reset server module state for a fresh test."""
    server._state = WorldState()
    server._rng = random.Random(42)
    server._recent_salience_kinds = set()
    server._state.last_surface = surface


# ══════════════════════════════════════════════════════════════════════════
# _bearing_to_label
# ══════════════════════════════════════════════════════════════════════════


def test_bearing_to_label_cardinal():
    assert server._bearing_to_label(0, None) == "北"
    assert server._bearing_to_label(90, None) == "东"
    assert server._bearing_to_label(180, None) == "南"
    assert server._bearing_to_label(270, None) == "西"


def test_bearing_to_label_intercardinal():
    assert server._bearing_to_label(45, None) == "东北"
    assert server._bearing_to_label(135, None) == "东南"
    assert server._bearing_to_label(225, None) == "西南"
    assert server._bearing_to_label(315, None) == "西北"


def test_bearing_to_label_semantic():
    assert server._bearing_to_label(None, "uphill") == "上山"
    assert server._bearing_to_label(None, "toward_sea") == "海边"
    assert server._bearing_to_label(None, "forward") is None
    assert server._bearing_to_label(None, None) is None


def test_bearing_to_label_none():
    assert server._bearing_to_label(None, None) is None


# ══════════════════════════════════════════════════════════════════════════
# _build_walk_narrative: direction tracking
# ══════════════════════════════════════════════════════════════════════════


def test_first_step_sets_direction():
    _reset_state()
    rng = random.Random(42)
    step = {"dist_km": 2.0, "new_surface": "grass", "slope_deg": 0}
    env = {"surface": "grass"}

    text = server._build_walk_narrative(step, env, 90.0, None, rng)

    # First step should set direction without "转身"
    assert "东" in text or text  # may or may not include direction text on first step
    assert server._state.narrative["direction"] == "东"
    # No "转身" on first step
    assert "转身" not in text


def test_direction_change_adds_transition():
    _reset_state()
    rng = random.Random(42)
    server._state.narrative["direction"] = "东"

    step = {"dist_km": 2.0, "new_surface": "grass", "slope_deg": 0}
    env = {"surface": "grass"}

    text = server._build_walk_narrative(step, env, 180.0, None, rng)

    # Direction changed from 东 to 南 → should have "转身"
    assert "转身" in text
    assert "南" in text
    assert server._state.narrative["direction"] == "南"


def test_same_direction_no_transition():
    _reset_state()
    rng = random.Random(42)
    server._state.narrative["direction"] = "东"

    step = {"dist_km": 2.0, "new_surface": "grass", "slope_deg": 0}
    env = {"surface": "grass"}

    text = server._build_walk_narrative(step, env, 90.0, None, rng)

    # Same direction → no "转身"
    assert "转身" not in text


def test_direction_change_resets_distance():
    _reset_state()
    rng = random.Random(42)
    server._state.narrative["direction"] = "东"
    server._state.narrative["distance_walked"] = 8000

    step = {"dist_km": 2.0, "new_surface": "grass", "slope_deg": 0}
    env = {"surface": "grass"}

    server._build_walk_narrative(step, env, 180.0, None, rng)

    # Direction change should reset distance
    assert server._state.narrative["distance_walked"] == 2000.0


# ══════════════════════════════════════════════════════════════════════════
# _build_walk_narrative: terrain transitions
# ══════════════════════════════════════════════════════════════════════════


def test_terrain_change_detected():
    _reset_state("grass")
    rng = random.Random(42)
    server._state.narrative["direction"] = "东"

    step = {"dist_km": 2.0, "new_surface": "sand", "slope_deg": 0}
    env = {"surface": "sand"}

    text = server._build_walk_narrative(step, env, 90.0, None, rng)

    # Surface changed from grass to sand
    assert "沙" in text


def test_terrain_change_with_slope():
    _reset_state("grass")
    rng = random.Random(42)
    server._state.narrative["direction"] = "东"

    step = {"dist_km": 2.0, "new_surface": "rock", "slope_deg": 20}
    env = {"surface": "rock"}

    text = server._build_walk_narrative(step, env, 90.0, None, rng)

    # Steep slope → "爬升"
    assert "爬升" in text or "岩石" in text


def test_no_terrain_change():
    _reset_state("grass")
    rng = random.Random(42)
    server._state.narrative["direction"] = "东"

    step = {"dist_km": 2.0, "new_surface": "grass", "slope_deg": 0}
    env = {"surface": "grass"}

    text = server._build_walk_narrative(step, env, 90.0, None, rng)

    # Same surface → no terrain transition text
    assert "地面" not in text


# ══════════════════════════════════════════════════════════════════════════
# _build_walk_narrative: distance accumulation
# ══════════════════════════════════════════════════════════════════════════


def test_distance_accumulates():
    _reset_state()
    rng = random.Random(42)
    server._state.narrative["direction"] = "东"

    step = {"dist_km": 2.0, "new_surface": "grass", "slope_deg": 0}
    env = {"surface": "grass"}

    server._build_walk_narrative(step, env, 90.0, None, rng)
    assert server._state.narrative["distance_walked"] == 2000.0

    server._build_walk_narrative(step, env, 90.0, None, rng)
    assert server._state.narrative["distance_walked"] == 4000.0


def test_long_distance_reported():
    _reset_state()
    rng = random.Random(99)  # seed that doesn't trigger discovery/time/body
    server._state.narrative["direction"] = "东"
    server._state.narrative["distance_walked"] = 9000

    step = {"dist_km": 2.0, "new_surface": "grass", "slope_deg": 0}
    env = {"surface": "grass"}

    text = server._build_walk_narrative(step, env, 90.0, None, rng)

    # > 10km should trigger distance report
    assert "公里" in text
    # Distance should reset after report
    assert server._state.narrative["distance_walked"] == 0


# ══════════════════════════════════════════════════════════════════════════
# _build_walk_narrative: discovery
# ══════════════════════════════════════════════════════════════════════════


def test_discovery_after_pacing():
    """Discovery should only appear after 2+ steps and with some probability."""
    _reset_state()
    rng = random.Random(42)
    server._state.narrative["direction"] = "东"
    server._state.steps_since_discovery = 5  # well past pacing threshold

    step = {"dist_km": 2.0, "new_surface": "grass", "slope_deg": 0}
    env = {"surface": "grass"}

    # Run multiple times to check probability
    found_discovery = False
    for seed in range(100):
        rng = random.Random(seed)
        server._state.steps_since_discovery = 5
        text = server._build_walk_narrative(step, env, 90.0, None, rng)
        if len(text) > 50:  # discovery lines are longer
            found_discovery = True
            break

    # With 100 seeds and 40% probability, we should find at least one
    assert found_discovery


def test_discovery_tracked_in_state():
    _reset_state()
    rng = random.Random(42)
    server._state.narrative["direction"] = "东"
    server._state.steps_since_discovery = 5

    step = {"dist_km": 2.0, "new_surface": "grass", "slope_deg": 0}
    env = {"surface": "grass"}

    # Try enough seeds to get a discovery
    for seed in range(200):
        rng = random.Random(seed)
        server._state.steps_since_discovery = 5
        server._state.narrative["discoveries"] = []
        text = server._build_walk_narrative(step, env, 90.0, None, rng)
        if server._state.narrative["discoveries"]:
            assert server._state.narrative["last_feature"] is not None
            break
    else:
        # Probabilistic — if no discovery in 200 seeds, that's very unlikely
        assert False, "No discovery found in 200 seeds (expected ~80)"


# ══════════════════════════════════════════════════════════════════════════
# _build_walk_narrative: time flow & body state
# ══════════════════════════════════════════════════════════════════════════


def test_time_flow_lines_present():
    """With enough seeds, time flow lines should appear."""
    _reset_state()
    step = {"dist_km": 2.0, "new_surface": "grass", "slope_deg": 0}
    env = {"surface": "grass"}

    found_time = False
    for seed in range(100):
        rng = random.Random(seed)
        server._state.narrative["direction"] = "东"
        text = server._build_walk_narrative(step, env, 90.0, None, rng)
        time_words = ["太阳", "天色", "影子", "风向", "云层", "光线"]
        if any(w in text for w in time_words):
            found_time = True
            break

    assert found_time


def test_body_state_lines_present():
    """With enough seeds, body state lines should appear."""
    _reset_state()
    step = {"dist_km": 2.0, "new_surface": "grass", "slope_deg": 0}
    env = {"surface": "grass"}

    found_body = False
    for seed in range(200):
        rng = random.Random(seed)
        server._state.narrative["direction"] = "东"
        text = server._build_walk_narrative(step, env, 90.0, None, rng)
        body_words = ["嘴唇", "出汗", "腿", "吸气", "脚底", "额头"]
        if any(w in text for w in body_words):
            found_body = True
            break

    assert found_body


# ══════════════════════════════════════════════════════════════════════════
# Narrative state persistence
# ══════════════════════════════════════════════════════════════════════════


def test_narrative_state_defaults():
    """Fresh WorldState should have default narrative."""
    s = WorldState()
    assert s.narrative["direction"] is None
    assert s.narrative["distance_walked"] == 0
    assert s.narrative["last_feature"] is None
    assert s.narrative["discoveries"] == []
    assert s.narrative["mood"] == "neutral"


def test_narrative_state_roundtrip(tmp_path, monkeypatch):
    """Narrative state should survive save/load cycle."""
    import nowhere.state as state_mod

    monkeypatch.setattr(state_mod, "_SAVE_DIR", tmp_path)
    monkeypatch.setattr(state_mod, "_SAVE_FILE", tmp_path / "journey.json")

    s = WorldState()
    s.pos = (35.0, 139.0)
    s.narrative = {
        "direction": "东",
        "distance_walked": 4500.0,
        "last_feature": "test feature",
        "discoveries": ["a", "b"],
        "mood": "tired",
    }
    s.save()

    loaded = WorldState.load()
    assert loaded is not None
    assert loaded.narrative["direction"] == "东"
    assert loaded.narrative["distance_walked"] == 4500.0
    assert loaded.narrative["last_feature"] == "test feature"
    assert loaded.narrative["discoveries"] == ["a", "b"]
    assert loaded.narrative["mood"] == "tired"


def test_narrative_backward_compat(tmp_path, monkeypatch):
    """Old save files without narrative field should load with defaults."""
    import json

    import nowhere.state as state_mod

    monkeypatch.setattr(state_mod, "_SAVE_DIR", tmp_path)
    monkeypatch.setattr(state_mod, "_SAVE_FILE", tmp_path / "journey.json")

    # Simulate old save without narrative field
    old_data = {
        "pos": [35.0, 139.0],
        "path": [],
        "landed_at": None,
        "elapsed_hours": 0.0,
        "mode": "land",
    }
    (tmp_path / "journey.json").write_text(json.dumps(old_data), encoding="utf-8")

    loaded = WorldState.load()
    assert loaded is not None
    assert loaded.narrative["direction"] is None
    assert loaded.narrative["discoveries"] == []
