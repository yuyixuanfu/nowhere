"""Tests for playground CLI parameter parsing (B7)."""

from __future__ import annotations

from nowhere.playground import _parse_walk, _parse_listen


def test_walk_direction_only():
    """walk with just a direction defaults to 2.0 km."""
    direction, distance = _parse_walk("N")
    assert direction == "N"
    assert distance == 2.0


def test_walk_with_distance():
    """B7: walk N 3.5 must parse both direction and distance."""
    direction, distance = _parse_walk("N 3.5")
    assert direction == "N"
    assert distance == 3.5


def test_walk_with_float_distance():
    direction, distance = _parse_walk("SE 0.5")
    assert direction == "SE"
    assert distance == 0.5


def test_walk_empty():
    direction, distance = _parse_walk("")
    assert direction == "forward"
    assert distance == 2.0


def test_listen_default_seconds():
    """listen with no arg defaults to 10 seconds."""
    seconds = _parse_listen("")
    assert seconds == 10


def test_listen_with_seconds():
    """B7: listen 5 must parse 5 seconds."""
    seconds = _parse_listen("5")
    assert seconds == 5


def test_listen_with_30_seconds():
    seconds = _parse_listen("30")
    assert seconds == 30
