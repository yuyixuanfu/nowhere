"""Tests for places (named locations) and walk_to."""

from __future__ import annotations

import random

from nowhere import places, server


def test_patch_hit():
    r = places.find("缙云山", near=(29.56, 106.55))  # 从重庆找
    assert r is not None and r["type"] == "山"
    assert r["distance_km"] < 100 and r["bearing"]


def test_patch_great_wall():
    r = places.find("长城(八达岭)")
    assert r is not None and abs(r["lat"] - 40.359) < 0.01


def test_find_unknown_returns_none():
    assert places.find("绝不存在的地方xyz") is None


def test_bearing_word():
    assert places._bearing_word(0) == "北"
    assert places._bearing_word(90) == "东"
    assert places._bearing_word(225) == "西南"


async def test_walk_to_too_far():
    s = server._state
    s.pos = (39.47, 75.98)  # 喀什
    s.place_name = "喀什"
    r = await server.walk_to_impl("卢浮宫")
    assert r["data"]["error"] == "too_far"


async def test_walk_to_arrives():
    s = server._state
    s.pos = (29.56, 106.40)  # 缙云山附近
    s.place_name = "重庆"
    s.mode = "land"
    r = await server.walk_to_impl("缙云山")
    assert "到了" in r["text"] or "还剩" in r["text"]
    assert r["data"]["steps"] >= 1


async def test_walk_to_not_found():
    s = server._state
    s.pos = (39.47, 75.98)
    r = await server.walk_to_impl("绝不存在的地方xyz")
    assert r["data"]["error"] == "not_found"
