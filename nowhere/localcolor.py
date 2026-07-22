"""方志一叠卡——每个落点的本地颜色。

两层卡:
- 手写层(localcolor.json): 又又亲笔,64 地,品味活
- 烘焙层(baked.py): Wikidata 美食 + iNat 植被,收割一次永久离线

机制: 见过的不重复,抽完手写抽烘焙,都抽完就没了——
熟悉是抽卡抽出来的。
"""

from __future__ import annotations

import json
import pathlib
import random

from nowhere import baked

_DATA = pathlib.Path(__file__).resolve().parent / "data" / "localcolor.json"

_color: dict | None = None


def _load() -> dict:
    global _color
    if _color is None:
        _color = json.loads(_DATA.read_text(encoding="utf-8")) if _DATA.exists() else {}
    return _color


def has_place(place_name: str | None) -> bool:
    """手写层或烘焙层有货就算有这个地方。"""
    if not place_name:
        return False
    return place_name in _load() or bool(baked.flora_items(place_name))


def draw(
    place_name: str | None,
    seen: set[str],
    rng: random.Random,
    local_hour: int | None = None,
    country_code: str | None = None,
) -> dict | None:
    """抽一张没见过的卡 {"category", "text", "key"};抽完或无此地 → None。

    local_hour 在饭点(6-9/11-13/17-21)时美食卡权重翻倍——饭点遇见吃的。
    """
    if not place_name:
        return None

    # 候选池: (category, key, text, weight)
    pool: list[tuple[str, str, str, float]] = []

    entry = _load().get(place_name)
    if entry:
        for cat in ("物产", "声音", "痕迹", "植被", "美食"):
            for i, text in enumerate(entry.get(cat, [])):
                key = f"{place_name}/{cat}/{i}"
                if key not in seen:
                    w = 2.0 if cat == "美食" else 1.0
                    pool.append((cat, key, text, w))

    for i, item in enumerate(baked.food_items(country_code)):
        key = f"{place_name}/烘焙美食/{i}"
        if key not in seen:
            pool.append(("美食", key, baked.render_food(item, rng), 2.0))

    for i, item in enumerate(baked.flora_items(place_name)):
        key = f"{place_name}/烘焙植被/{i}"
        if key not in seen:
            pool.append(("植被", key, baked.render_flora(item, rng), 1.0))

    if not pool:
        return None

    meal_time = local_hour is not None and (
        6 <= local_hour < 9 or 11 <= local_hour < 13 or 17 <= local_hour < 21
    )
    weights = [w if (meal_time or cat != "美食") else 1.0 for cat, _, _, w in pool]
    total = sum(weights)
    r = rng.uniform(0, total)
    for (cat, key, text, _), w in zip(pool, weights):
        r -= w
        if r <= 0:
            return {"category": cat, "text": text, "key": key}
    cat, key, text, _ = pool[-1]
    return {"category": cat, "text": text, "key": key}


def rhythm_event(
    place_name: str | None,
    local_hour: int,
    rng: random.Random,
    month: int | None = None,
) -> str | None:
    """当前时刻命中的节律文案,没有 → None。

    卡可带 "months": [月份列表],带了就只在那些月出现(极昼/极光/
    三文鱼季这种季节限定);没带 = 全年有效。
    """
    if not place_name:
        return None
    entry = _load().get(place_name)
    if not entry:
        return None
    hits = []
    for r in entry.get("节律", []):
        if isinstance(r, str):
            # Plain string: always eligible (no hour/month filter)
            hits.append(r)
            continue
        if not (r["hours"][0] <= local_hour < r["hours"][1]):
            continue
        months = r.get("months")
        if months and month is not None and month not in months:
            continue
        hits.append(r["text"])
    return rng.choice(hits) if hits else None
