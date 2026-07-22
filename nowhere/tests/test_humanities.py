"""Tests for humanities (人文层一叠卡)."""

from __future__ import annotations

import random

from nowhere import humanities


def test_has_place():
    assert humanities.has_place("京都") is True
    assert humanities.has_place("南京") is True
    assert humanities.has_place("不存在的地方") is False
    assert humanities.has_place(None) is False


def test_alias():
    assert humanities.has_place("Skjolden") is True
    assert humanities.has_place("Sils Maria") is True
    assert humanities.has_place("Concord") is True


def test_draw_event_first():
    """先事、再人、后作品——这地方先是真的。"""
    seen: set[str] = set()
    rng = random.Random(1)
    c = humanities.draw("敦刻尔克", seen, rng)
    assert c is not None
    assert c["category"] == "事件"
    seen.add(c["key"])
    c2 = humanities.draw("敦刻尔克", seen, rng)
    assert c2 is not None
    assert c2["category"] == "作品"


def test_draw_no_repeat_and_exhaust():
    seen: set[str] = set()
    rng = random.Random(1)
    keys = []
    while True:
        c = humanities.draw("京都", seen, rng)
        if c is None:
            break
        assert c["key"] not in seen
        keys.append(c["key"])
        seen.add(c["key"])
    assert len(keys) >= 2  # 京都: 事件+人物+作品(数量可能随数据更新变化)
    assert humanities.draw("京都", seen, random.Random(1)) is None  # 抽完就没了


def test_draw_unknown_place():
    assert humanities.draw("不存在的地方", set(), random.Random(1)) is None
    assert humanities.draw(None, set(), random.Random(1)) is None


def test_ref_carries_ask_hook():
    """ref 带 name/title——追问走 ask 用。先事(事件)再人(人物)后作品。"""
    # 巴黎先有事件卡(攻占巴士底狱/巴黎公社)
    c = humanities.draw("巴黎", set(), random.Random(1))
    assert c is not None
    assert c["category"] == "事件"
    assert "name" in c["ref"]
    assert "text" not in c["ref"]

    # 伦敦有人物卡(伍尔夫/普拉斯),但先有事件卡(伦敦大火)
    c2 = humanities.draw("伦敦", set(), random.Random(1))
    assert c2 is not None
    assert c2["category"] == "事件"
    assert "text" not in c2["ref"]


def test_event_cards_sober():
    """事件层卡不幽默——人工审,这里只保结构: 事件卡必须有 year。"""
    for name, entry in humanities._load()["places"].items():
        for card in entry.get("事件", []):
            assert card.get("year"), f"{name} 事件卡缺 year"
            assert card.get("name"), f"{name} 事件卡缺 name"
            assert card.get("text"), f"{name} 事件卡缺 text"


# ── 坐标绑定测试 ────────────────────────────────────────────────────


def test_geocoded_coords_exist():
    """主要地名必须有 lat/lon。"""
    data = humanities._load()
    major = ["京都", "巴黎", "纽约", "赤壁", "成都", "马丘比丘", "开罗", "伦敦"]
    for name in major:
        entry = data["places"].get(name, {})
        assert "lat" in entry, f"{name} 缺 lat"
        assert "lon" in entry, f"{name} 缺 lon"
        assert isinstance(entry["lat"], (int, float))
        assert isinstance(entry["lon"], (int, float))


def test_all_places_have_coords():
    """所有 178 个地名都必须有坐标。"""
    data = humanities._load()
    missing = [k for k, v in data["places"].items() if "lat" not in v or "lon" not in v]
    assert missing == [], f"缺坐标: {missing}"


# ── 近距离触发测试 ──────────────────────────────────────────────────


def test_nearby_triggers_京都():
    """在京都附近(35.01, 135.77)应该触发京都的人文卡。"""
    rng = random.Random(42)
    seen: set[str] = set()
    # 京都坐标: 35.0116, 135.7681
    result = humanities.nearby_place(35.01, 135.77, seen, rng)
    assert result is not None
    assert result["place"] == "京都"
    assert result["category"] == "事件"  # 优先事件
    assert result["text"]
    assert result["key"]


def test_nearby_no_trigger_far_away():
    """远离所有人名地点(南太平洋)不应触发。"""
    rng = random.Random(42)
    seen: set[str] = set()
    result = humanities.nearby_place(-50.0, -150.0, seen, rng)
    assert result is None


def test_nearby_seen_no_repeat():
    """已见卡不重复触发。"""
    rng = random.Random(42)
    seen: set[str] = set()

    # 第一次触发
    r1 = humanities.nearby_place(35.01, 135.77, seen, rng)
    assert r1 is not None
    seen.add(r1["key"])

    # 第二次: 如果京都只有一张事件卡,draw 会抽人物或作品
    r2 = humanities.nearby_place(35.01, 135.77, seen, rng)
    if r2 is not None:
        assert r2["key"] not in seen


def test_nearby_exhaust_all_cards():
    """所有卡都见过后,不再触发。"""
    rng = random.Random(42)
    seen: set[str] = set()
    keys = []
    for _ in range(20):  # 京都卡数有限
        r = humanities.nearby_place(35.01, 135.77, seen, rng)
        if r is None:
            break
        assert r["key"] not in seen
        keys.append(r["key"])
        seen.add(r["key"])
    assert len(keys) >= 2  # 京都至少有事件+人物+作品
    # 抽完后不再触发
    assert humanities.nearby_place(35.01, 135.77, seen, rng) is None


def test_nearby_destination_priority():
    """walk_to 目的地在范围内时优先选它。"""
    rng = random.Random(42)
    seen: set[str] = set()
    # 赤壁坐标: 29.7167, 113.8833; 荆州: 30.3261, 112.2391
    # 在赤壁附近,指定 destination="赤壁"
    result = humanities.nearby_place(29.72, 113.88, seen, rng, destination="赤壁")
    if result is not None:
        assert result["place"] == "赤壁"


def test_nearby_event_before_works():
    """同类地名,事件优先于作品。"""
    rng = random.Random(42)
    seen: set[str] = set()
    # 敦刻尔克坐标: 51.0343, 2.3768
    result = humanities.nearby_place(51.03, 2.38, seen, rng)
    assert result is not None
    assert result["category"] == "事件"
