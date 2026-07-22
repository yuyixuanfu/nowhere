"""Tests for observer feeds 接线: 落点/目击/邮戳补字段."""

from __future__ import annotations

import asyncio
import random

import pytest

from nowhere import placememory, server
from nowhere.state import WorldState


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    server._state = WorldState()
    yield
    server._state = WorldState()


def test_postmark_has_surface_and_phase():
    server._state.place_name = "试验地"
    server._state.last_env = {
        "terrain": {"surface": "forest"},
        "sky": {"phase": "day"},
        "weather": {"text": "晴", "temp_c": 20},
    }
    stamp = server._postmark(35.0, 135.7)
    assert stamp["surface"] == "forest"
    assert stamp["phase"] == "day"


def test_postmark_defaults_when_no_env():
    server._state.place_name = None
    server._state.last_env = None
    stamp = server._postmark(35.0, 135.7)
    assert stamp["surface"] == "grass"
    assert stamp["phase"] == "day"


def test_look_around_records_sighting(monkeypatch):
    """强制走 life 分支: 方志无货,rng.random 交替返回跳过美食/命中生命。"""
    from nowhere import localcolor

    monkeypatch.setattr(localcolor, "has_place", lambda _p: False)

    async def fake_nearby(*a, **kw):
        return {
            "name": "Rana temporaria", "common_name": "林蛙",
            "seen_at": "2026-07-14", "distance_m": 1600,
            "photo_url": None, "unit": "一只",
        }

    monkeypatch.setattr(server.life, "nearby", fake_nearby)

    class _R:
        def __init__(self):
            self._n = 0
        def random(self):
            self._n += 1
            # 1st call: food check (>= 0.15 to skip food branch)
            # 2nd call: life check (< 0.85 to enter life branch)
            return 0.2 if self._n == 1 else 0.1

        def choice(self, seq):
            return seq[0]

        def uniform(self, a, b):
            return a

        def randint(self, a, b):
            return a

        def shuffle(self, lst):
            pass  # in-place, no-op for testing

    server._state.pos = (35.0, 135.7)
    server._state.place_name = "试验地"
    server._rng = _R()
    try:
        r = asyncio.run(server.look_around_impl())
    finally:
        server._rng = random.Random()
    assert "林蛙" in r["text"] or r["data"]["encounters"][0]["common_name"] == "林蛙"
    items = placememory.sightings()
    assert len(items) == 1
    assert items[0]["common_name"] == "林蛙"
    assert items[0]["lat"] == pytest.approx(35.0)
