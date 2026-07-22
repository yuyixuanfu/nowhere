"""TDD tests for B8/B9/B10: parameter validation fixes."""

from __future__ import annotations

import random

import httpx
import pytest

from nowhere import server


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module-level state before each test."""
    import nowhere.state as state_mod

    server._state = state_mod.WorldState()
    server._rng = random.Random(42)
    server._recent_salience_kinds = set()


# ── B8: mark empty name ──────────────────────────────────────────────


class TestB8MarkEmptyName:
    def test_mark_empty_string_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
        # Need to land first
        server._state.pos = (35.0, 139.0)
        r = server.mark_impl("", "note")
        assert r["data"]["error"] == "empty_name"

    def test_mark_whitespace_only_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
        server._state.pos = (35.0, 139.0)
        r = server.mark_impl("   ", "note")
        assert r["data"]["error"] == "empty_name"

    def test_mark_valid_name_still_works(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
        server._state.pos = (35.0, 139.0)
        r = server.mark_impl("my_spot", "a note")
        assert "error" not in r["data"]
        assert r["data"]["name"] == "my_spot"


# ── B9: walk invalid direction warning ────────────────────────────────


class TestB9WalkInvalidDirection:
    @pytest.mark.asyncio
    async def test_invalid_direction_sets_warning(self, respx_mock, tmp_path, monkeypatch):
        monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
        respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
        await server.open_door_impl()
        r = await server.walk_impl("INVALID", 2.0)
        assert r["data"].get("direction_warning") is True

    @pytest.mark.asyncio
    async def test_valid_direction_no_warning(self, respx_mock, tmp_path, monkeypatch):
        monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
        respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
        await server.open_door_impl()
        r = await server.walk_impl("N", 2.0)
        assert "direction_warning" not in r["data"]


# ── B10: listen seconds validation ────────────────────────────────────


class TestB10ListenSeconds:
    @pytest.mark.asyncio
    async def test_listen_zero_seconds_error(self, respx_mock, tmp_path, monkeypatch):
        monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
        respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
        await server.open_door_impl()
        r = await server.listen_impl(0)
        assert r["data"]["error"] == "bad_seconds"

    @pytest.mark.asyncio
    async def test_listen_negative_seconds_error(self, respx_mock, tmp_path, monkeypatch):
        monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
        respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
        await server.open_door_impl()
        r = await server.listen_impl(-1)
        assert r["data"]["error"] == "bad_seconds"

    @pytest.mark.asyncio
    async def test_listen_over_60_clamps(self, respx_mock, tmp_path, monkeypatch):
        monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
        respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
        await server.open_door_impl()
        # listen_impl(100) should succeed (clamped to 60), not error
        r = await server.listen_impl(100)
        assert "error" not in r["data"], "100 seconds should be clamped, not errored"


# ── B10: walk distance clamping ───────────────────────────────────────


class TestB10WalkDistanceClamping:
    @pytest.mark.asyncio
    async def test_walk_huge_distance_clamped(self, respx_mock, tmp_path, monkeypatch):
        monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
        respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
        await server.open_door_impl()
        r = await server.walk_impl("N", 999)
        assert r["data"]["step"].get("clamped") is True
