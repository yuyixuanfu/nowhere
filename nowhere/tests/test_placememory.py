"""Tests for placememory landings/sightings 编录."""

from __future__ import annotations

import pytest

from nowhere import placememory


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    """绝不碰生产 ~/.nowhere——重定向到 tmp。"""
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))


def test_record_landing_counts_and_coords():
    assert placememory.record_landing("京都", 35.0, 135.7) == 1
    assert placememory.record_landing("京都", 35.0, 135.7) == 2
    items = placememory.landings()
    assert len(items) == 1
    it = items[0]
    assert it["place"] == "京都"
    assert it["lat"] == pytest.approx(35.0)
    assert it["lon"] == pytest.approx(135.7)
    assert it["count"] == 2
    assert it["last"]  # iso 时间串非空


def test_landings_empty(tmp_path):
    assert placememory.landings() == []


def test_record_sighting_and_list():
    placememory.record_sighting(
        name="Crocothemis servilia", common_name="红蜻",
        lat=35.0, lon=135.7, distance_m=1600, seen_at="2026-07-14",
        source="inaturalist",
    )
    items = placememory.sightings()
    assert len(items) == 1
    it = items[0]
    assert it["common_name"] == "红蜻"
    assert it["lat"] == pytest.approx(35.0)
    assert it["distance_m"] == 1600
    assert it["source"] == "inaturalist"
    assert it["ts"]


def test_sightings_cap_200():
    for i in range(205):
        placememory.record_sighting(
            name=f"sp{i}", common_name="", lat=0.0, lon=0.0,
            distance_m=None, seen_at="", source="test",
        )
    assert len(placememory.sightings()) == 200


def test_record_landing_with_terrain():
    placememory.record_landing("珠峰", 27.98, 86.92, elevation=8848, surface="rock")
    it = placememory.landings()[0]
    assert it["elevation"] == 8848
    assert it["surface"] == "rock"


def test_postcards_roundtrip():
    card = {"id": 9, "text": "想你", "stamp": {"place": "蒙特维多"}, "replies": [], "front_img": None}
    placememory.save_postcard(card)
    items = placememory.postcards()
    assert items[0]["id"] == 9
    assert items[0]["stamp"]["place"] == "蒙特维多"


def test_postcard_reply_persists():
    card = {"id": 10, "text": "t", "stamp": {"place": "x"}, "replies": []}
    placememory.save_postcard(card)
    assert placememory.add_postcard_reply(10, "回你") is True
    assert placememory.add_postcard_reply(999, "没这张") is False
    items = placememory.postcards()
    assert items[0]["replies"] == ["回你"]


def test_update_postcard_front_img():
    card = {"id": 11, "text": "t", "stamp": {"place": "x"}, "replies": [], "front_img": None}
    placememory.save_postcard(card)
    card["front_img"] = "/static/postcards/card_11.png"
    placememory.update_postcard(card)
    assert placememory.postcards()[0]["front_img"].endswith("card_11.png")
