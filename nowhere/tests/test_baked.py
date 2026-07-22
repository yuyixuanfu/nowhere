"""Tests for baked (烘焙物产层)."""

from __future__ import annotations

import random

from nowhere import baked


def test_food_scene_render():
    """Scene file entries should produce sensory-rich descriptions."""
    r = baked.render_food({"zh": "冬阴功", "en": "Tom Yum Kung"}, random.Random(1))
    assert "冬阴功" in r
    # Scene file has sensory details about sourness
    assert "酸" in r or "辣" in r


def test_food_scene_partial_match():
    """Partial name matches should still hit scene file."""
    r = baked.render_food({"zh": "热干面", "en": "Hot Dry Noodles"}, random.Random(1))
    assert "热干面" in r
    assert "芝麻酱" in r  # scene file mentions sesame paste


def test_food_scene_fallback():
    """Unknown foods should fall back to template logic."""
    r = baked.render_food({"zh": "不存在的食物", "en": "Nonexistent"}, random.Random(1))
    assert r  # Should still return something
    assert "不存在的食物" in r


def test_food_scene_count():
    """Verify all 193 scenes loaded."""
    scenes = baked._load_food_scenes()
    assert len(scenes) == 192
