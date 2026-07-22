"""env 惯性缓存: 3km/30min 内不重拉。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from nowhere import server
from nowhere.state import WorldState


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    server._state = WorldState()
    yield
    server._state = WorldState()


def _fake_gather(counter):
    async def fake(lat, lon, dt):
        counter.append((lat, lon))
        return {"elevation": 100.0, "surface": "grass", "sky": {},
                "weather": {}, "radio": None, "water_features": []}
    return fake


def test_cache_hit_within_3km(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_gather_env", _fake_gather(calls))
    dt = datetime.now(timezone.utc)
    asyncio.run(server._gather_env_cached(35.0, 135.7, dt))
    asyncio.run(server._gather_env_cached(35.01, 135.71, dt))  # ~1.4km
    assert len(calls) == 1  # 第二次没重拉


def test_cache_miss_beyond_3km(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_gather_env", _fake_gather(calls))
    dt = datetime.now(timezone.utc)
    asyncio.run(server._gather_env_cached(35.0, 135.7, dt))
    asyncio.run(server._gather_env_cached(35.1, 135.7, dt))  # ~10km
    assert len(calls) == 2


def test_cache_miss_after_30min(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_gather_env", _fake_gather(calls))
    dt = datetime.now(timezone.utc)
    asyncio.run(server._gather_env_cached(35.0, 135.7, dt))
    asyncio.run(server._gather_env_cached(35.0, 135.7, dt + timedelta(minutes=31)))
    assert len(calls) == 2


def _full_fake_gather(counter):
    async def fake(lat, lon, dt):
        counter.append((lat, lon))
        return {
            "elevation": 100.0,
            "surface": "grass",
            "sky": {"phase": "day"},
            "weather": {"temp_c": 15.0, "wind_ms": 2.0, "text": "", "feels_c": 14.0},
            "radio": None,
            "water_features": [],
        }
    return fake


def test_walk_quiet_when_cached_and_no_change(monkeypatch):
    """缓存命中+世界没变 → 留白短句,不长篇。"""
    calls = []
    monkeypatch.setattr(server, "_gather_env", _full_fake_gather(calls))
    server._state.pos = (35.0, 135.7)
    server._state.place_name = "试验地"
    server._state.landed_at = datetime.now(timezone.utc)
    server._state.last_env = None
    server._state.messages = []

    class _Stub:
        def random(self): return 0.99      # encounter 全部不中
        def choice(self, seq): return seq[0]
        def uniform(self, a, b): return a
        def randint(self, a, b): return a

    # silence encounter sources so sections stays empty on the second walk
    async def _empty_water(*a, **kw): return []
    def _no_humanities(*a, **kw): return None  # humanities.nearby_place 是 sync
    monkeypatch.setattr(server.hydrology, "nearby_water", _empty_water)
    monkeypatch.setattr(server.humanities, "nearby_place", _no_humanities)
    monkeypatch.setattr(server.localcolor, "has_place", lambda *a, **kw: False)

    old_rng = server._rng
    server._rng = _Stub()
    try:
        asyncio.run(server.walk_impl("N", 2.0))   # 第一次:全量
        r = asyncio.run(server.walk_impl("N", 0.5))  # 第二次:缓存命中
    finally:
        server._rng = old_rng
    assert r["text"] in server._QUIET_WALK
    assert len(calls) == 1
