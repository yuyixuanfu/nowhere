"""Tests for the web observer layer."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from nowhere import server, web


@pytest.fixture(autouse=True)
def _clean_messages():
    """Reset _state to a clean WorldState before each test.

    open_door_impl replaces server._state with a new object, so we must
    swap it back to a pristine instance to avoid cross-test contamination.
    """
    from nowhere.state import WorldState
    server._state = WorldState()
    yield


def test_state_endpoint():
    c = TestClient(web.app)
    r = c.get("/state")
    assert r.status_code == 200
    body = r.json()
    assert "pos" in body
    assert "path" in body
    assert "mode" in body
    assert "last_text" in body


def test_state_pos_null_before_landing():
    c = TestClient(web.app)
    body = c.get("/state").json()
    assert body["pos"] is None


def test_post_message():
    c = TestClient(web.app)
    r = c.post("/message", json={"content": "替我看一眼海"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    msgs = c.get("/messages").json()
    assert any(
        m["content"] == "替我看一眼海" and m["encountered"] is False
        for m in msgs
    )


def test_post_empty_message_rejected():
    c = TestClient(web.app)
    r = c.post("/message", json={"content": ""})
    assert r.status_code == 400


def test_messages_fifo_maxlen():
    """Messages deque is bounded at 20."""
    c = TestClient(web.app)
    for i in range(25):
        c.post("/message", json={"content": f"msg-{i}"})
    msgs = c.get("/messages").json()
    assert len(msgs) == 20
    # oldest dropped, newest kept
    assert msgs[-1]["content"] == "msg-24"


def test_index_returns_html():
    c = TestClient(web.app)
    r = c.get("/")
    assert r.status_code == 200
    assert "乌有乡" in r.text and "world110m.js" in r.text


def test_multiple_messages_order():
    c = TestClient(web.app)
    c.post("/message", json={"content": "first"})
    c.post("/message", json={"content": "second"})
    msgs = c.get("/messages").json()
    assert len(msgs) == 2
    assert msgs[0]["content"] == "first"
    assert msgs[1]["content"] == "second"


def test_history_marks_sightings_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    from nowhere import placememory, marks as marks_mod

    placememory.record_landing("京都", 35.0, 135.7)
    placememory.record_sighting(
        name="Rana", common_name="林蛙", lat=35.0, lon=135.7,
        distance_m=100, seen_at="2026-07-14", source="test",
    )
    marks_mod.save("测试标记", 35.0, 135.7, note="n")

    c = TestClient(web.app)
    h = c.get("/history").json()
    assert h["landings"][0]["place"] == "京都"
    assert "path" in h

    ms = c.get("/marks").json()
    assert any(m["name"] == "测试标记" for m in ms)

    ss = c.get("/sightings").json()
    assert ss[0]["common_name"] == "林蛙"


def test_static_world_map_served():
    c = TestClient(web.app)
    r = c.get("/static/world110m.js")
    assert r.status_code == 200
    assert r.text.startswith("window.WORLD_COUNTRIES")


def test_postcards_from_disk_not_memory(tmp_path, monkeypatch):
    """网页进程内存是空的,也要看到别的进程寄的明信片。"""
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    from nowhere import placememory

    placememory.save_postcard({
        "id": 42, "text": "别的进程寄的",
        "stamp": {"place": "蒙特维多"}, "replies": [], "front_img": None,
    })
    c = TestClient(web.app)
    cards = c.get("/postcards").json()
    assert any(cd["id"] == 42 and cd["text"] == "别的进程寄的" for cd in cards)


def test_reply_postcard_cross_process(tmp_path, monkeypatch):
    """卡在文件里不在内存: 回信也要成功。"""
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    from nowhere import placememory

    placememory.save_postcard({
        "id": 43, "text": "t", "stamp": {"place": "x"}, "replies": [],
    })
    c = TestClient(web.app)
    r = c.post("/postcard/43/reply", json={"content": "收到了"})
    assert r.status_code == 200 and r.json()["ok"] is True
    cards = c.get("/postcards").json()
    assert cards[0]["replies"] == ["收到了"]


def test_delete_postcard(tmp_path, monkeypatch):
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    from nowhere import placememory

    placememory.save_postcard({"id": 50, "text": "废卡", "stamp": {"place": "x"}, "replies": []})
    c = TestClient(web.app)
    assert len(c.get("/postcards").json()) == 1
    r = c.request("DELETE", "/postcard/50")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert c.get("/postcards").json() == []
    assert c.request("DELETE", "/postcard/50").status_code == 404
