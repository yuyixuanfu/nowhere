"""Tests for nowhere.sky — offline astronomical calculations."""

from datetime import datetime, timezone

import pytest

from nowhere import sky


def test_noon_sun_high_nairobi_equinox():
    dt = datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc)  # Nairobi UTC+3 ~noon
    r = sky.sun_moon(-1.29, 36.82, dt)
    assert r["sun_alt"] > 55 and r["phase"] == "day"


def test_midnight_is_night():
    dt = datetime(2026, 3, 20, 22, 0, tzinfo=timezone.utc)  # Nairobi ~1am
    assert sky.sun_moon(-1.29, 36.82, dt)["phase"] == "night"


def test_moon_phase_full():
    dt = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)  # 2026-07-29 full moon
    assert sky.sun_moon(0, 0, dt)["moon_phase"] > 0.95


def test_visible_sky_shape():
    dt = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    r = sky.visible_sky(-30.0, 145.0, dt)  # Australia inland night
    assert isinstance(r["planets"], list) and isinstance(r["milky_way_core_up"], bool)


def test_milky_way_core_up_australia_winter_night():
    dt = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)  # Australia ~22:00, core high
    assert sky.visible_sky(-30.0, 145.0, dt)["milky_way_core_up"] is True


def test_no_network(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket.socket,
        "connect",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no net")),
    )
    sky.sun_moon(0, 0, datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc))
