"""Integration test for nowhere.server -- full chain, all HTTP blacked out.

Every external call is routed to a respx timeout → fallback codepath must
survive without crashing.  This is the offline-core promise.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from nowhere import server


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module-level state before each test."""
    import nowhere.state as state_mod

    server._state = state_mod.WorldState()
    server._rng = __import__("random").Random(42)
    server._recent_salience_kinds = set()


# ── Full chain ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_full_chain(respx_mock: respx.MockRouter, tmp_path, monkeypatch):
    """open_door → walk x3 → listen → ask → mark → where_am_i.

    All HTTP is blacked out -- every provider hits its fallback path.
    """
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))

    # Global blackout: every HTTP request times out
    respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))

    # ── open_door (random landing) ───────────────────────────────────
    r1 = await server.open_door_impl()
    assert r1["text"], "open_door must produce prose"
    assert r1["data"]["position"], "open_door must return position"
    lat, lon = r1["data"]["position"]["lat"], r1["data"]["position"]["lon"]
    assert isinstance(lat, float)
    assert isinstance(lon, float)

    # ── walk x3 ──────────────────────────────────────────────────────
    for _ in range(3):
        rw = await server.walk_impl("N", 2.0)
        assert rw["text"], "walk must produce prose"
        assert "position" in rw["data"]

    # ── listen ───────────────────────────────────────────────────────
    rl = await server.listen_impl(3)
    assert "stream_url" in rl["data"], "listen data must include stream_url"
    # Fallback radio list provides a station even when all HTTP is down
    assert rl["data"]["stream_url"] is not None

    # ── ask ──────────────────────────────────────────────────────────
    ra = await server.ask_impl("这座山")
    assert ra["text"], "ask must produce text (fallback or real)"

    # ── mark + reopen ────────────────────────────────────────────────
    server.mark_impl("测试点", "链式")
    r2 = await server.open_door_impl(to="测试点")
    assert r2["text"], "open_door to mark must produce prose"
    # Position may differ (mark lookup vs random) -- just must not crash

    # ── where_am_i ───────────────────────────────────────────────────
    w = server.where_am_i_impl()
    assert "providers" in w["data"], "where_am_i must expose provider status"
    assert w["text"], "where_am_i must produce prose"


# ── Edge cases ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_walk_before_open():
    """walk before open_door returns an error message, not a crash."""
    r = await server.walk_impl("N", 2.0)
    assert "error" in r["data"]
    assert r["data"]["error"] == "not_landed"


def test_marks_empty(tmp_path, monkeypatch):
    """marks() on fresh state returns empty list."""
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    r = server.marks_impl()
    assert r["data"]["marks"] == []


def test_mark_duplicate_returns_error(tmp_path, monkeypatch):
    """mark_impl should catch duplicate ValueError and return an error dict."""
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    server._state.pos = (10.0, 20.0)  # fake landed
    server.mark_impl("dup", "first")
    r = server.mark_impl("dup", "second")
    assert r["data"]["error"] == "duplicate"
    assert r["data"]["existing"]["lat"] == 10.0
    assert "已经标过了" in r["text"]


@pytest.mark.asyncio
async def test_listen_before_open():
    """listen before open_door returns an error message."""
    r = await server.listen_impl(5)
    assert "error" in r["data"]


@pytest.mark.asyncio
async def test_look_around_before_open():
    """look_around before open_door returns an error message."""
    r = await server.look_around_impl()
    assert "error" in r["data"]
