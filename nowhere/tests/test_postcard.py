"""Tests for postcards (send + stamp + reply loop)."""

from __future__ import annotations

import httpx
from starlette.testclient import TestClient

from nowhere import server, web


import pytest


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    """绝不碰生产 ~/.nowhere(避雷28)。"""
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))


def _cur():
    """当前状态实例(open_door 会换新对象,必须动态取)。"""
    return server._state


def _reset_state():
    s = server._state
    s.pos = None
    s.path.clear()
    s.postcards.clear()
    s.messages.clear()
    s.last_env = None
    s.place_name = None


async def test_send_postcard_stamped(tmp_path, monkeypatch):
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    _reset_state()
    import respx
    with respx.mock:
        import respx as _r
        _r.route().mock(side_effect=httpx.TimeoutException("t"))
        await server.open_door_impl()
        r = server.send_postcard_impl("这里的风有沙子的味道")
    assert "邮戳" in r["text"]
    card = r["data"]
    assert card["text"] == "这里的风有沙子的味道"
    assert card["stamp"]["local_time"]
    assert "elevation" in card["stamp"]
    assert len(_cur().postcards) == 1


def test_send_postcard_needs_door():
    _reset_state()
    r = server.send_postcard_impl("喂")
    assert r["data"]["error"] == "not_landed"


def test_send_postcard_needs_text():
    _reset_state()
    _cur().pos = (35.0, 139.0)
    r = server.send_postcard_impl("   ")
    assert r["data"]["error"] == "empty"


def test_send_postcard_too_long():
    _reset_state()
    _cur().pos = (35.0, 139.0)
    r = server.send_postcard_impl("A" * 1001)
    assert r["data"]["error"] == "too_long"

    # 正好1000字应该可以
    r2 = server.send_postcard_impl("A" * 1000)
    assert "error" not in r2.get("data", {})


def test_reply_loops_back_to_messages():
    _reset_state()
    s = _cur()
    s.pos = (35.0, 139.0)
    s.postcards.append({"id": 1, "text": "x", "stamp": {}, "replies": []})
    r = server.reply_postcard_impl(1, "收到,替你高兴")
    assert r["ok"] is True
    assert s.postcards[0]["replies"] == ["收到,替你高兴"]
    assert any("回信" in m["content"] for m in s.messages)


def test_web_postcards_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    _reset_state()
    from nowhere import placememory

    placememory.save_postcard({"id": 1, "text": "从前线寄回", "stamp": {"place": "东京"}, "replies": []})
    c = TestClient(web.app)
    cards = c.get("/postcards").json()
    assert cards[0]["text"] == "从前线寄回"
    r = c.post("/postcard/1/reply", json={"content": "家里都好"})
    assert r.json()["ok"] is True
    assert c.get("/postcards").json()[0]["replies"] == ["家里都好"]
    assert c.post("/postcard/99/reply", json={"content": "x"}).status_code == 404
