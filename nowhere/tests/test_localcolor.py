"""Tests for localcolor (方志一叠卡)."""

from __future__ import annotations

import random

from nowhere import localcolor


def test_has_place():
    assert localcolor.has_place("喀什") is True
    assert localcolor.has_place("不存在的地方") is False
    assert localcolor.has_place(None) is False


def test_draw_no_repeat_until_exhausted():
    seen: set[str] = set()
    rng = random.Random(1)
    cards = []
    while True:
        c = localcolor.draw("喀什", seen, rng)
        if c is None:
            break
        seen.add(c["key"])
        cards.append(c)
    assert len(cards) >= 8  # 一叠,不是一张
    assert localcolor.draw("喀什", seen, rng) is None  # 抽完就没了


def test_draw_unknown_place():
    assert localcolor.draw("不存在的地方", set(), random.Random(1)) is None


def test_rhythm_event_hours():
    # 喀什: 10-22 巴扎开着
    hit = localcolor.rhythm_event("喀什", 15, random.Random(1))
    assert hit is not None and "巴扎" in hit
    # 深夜 3 点无节律
    assert localcolor.rhythm_event("喀什", 3, random.Random(1)) is None
    assert localcolor.rhythm_event("不存在的地方", 12, random.Random(1)) is None
