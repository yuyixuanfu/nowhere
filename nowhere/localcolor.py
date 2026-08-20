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
import sys

from nowhere import baked, cards as _cards

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"

_lc_cards: list[_cards.Card] | None = None

# ── Card 54: thin-place speed limit ────────────────────────────────────
_THIN_THRESHOLD: int = 5   # places with fewer effective cards are "thin"
_THIN_COOLDOWN: int = 3    # minimum steps between thin-place draws
_thin_last_draw: dict[str, int] = {}  # place_name -> walk_step of last draw


def _conditions_pass(
    c: _cards.Card,
    local_hour: int | None = None,
    month: int | None = None,
    weekday: int | None = None,
) -> bool:
    """Check hours / months / weekday conditions on a card.

    Returns False when the card's conditions explicitly exclude the current
    time context; returns True when all conditions are met or absent.
    """
    hours = c.conditions.get("hours")
    if hours and local_hour is not None and not (hours[0] <= local_hour < hours[1]):
        return False
    months = c.conditions.get("months")
    if months and month is not None and month not in months:
        return False
    weekdays = c.conditions.get("weekday")
    if weekdays is not None and weekday is not None and weekday not in weekdays:
        return False
    return True


def _place_card_count() -> dict[str, int]:
    """Count effective (non-rhythm) cards per place."""
    counts: dict[str, int] = {}
    for c in _load():
        if c.conditions.get("place") and c.meta.get("category") != "节律":
            place = c.conditions["place"]
            counts[place] = counts.get(place, 0) + 1
    return counts


def is_thin_place(place_name: str | None) -> bool:
    """A place is thin if it has fewer than _THIN_THRESHOLD effective cards."""
    if not place_name:
        return False
    return _place_card_count().get(place_name, 0) < _THIN_THRESHOLD


def _load() -> list[_cards.Card]:
    """Load localcolor cards via the unified Card layer.

    Merges five regional files (china/japan_korea_sea/americas_africa_oceania/
    europe_middleeast/natural) with the main localcolor.json.  Duplicate keys
    prefer the main file; regional-only entries are merged in.
    """
    global _lc_cards
    if _lc_cards is not None:
        return _lc_cards

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    _lc_cards = _cards.load_localcolor(_DATA_DIR)

    return _lc_cards


def _places_set() -> set[str]:
    """Get the set of place names that have localcolor cards."""
    return {c.conditions.get("place") for c in _load() if c.conditions.get("place")}


def has_place(place_name: str | None) -> bool:
    """手写层或烘焙层有货就算有这个地方。"""
    if not place_name:
        return False
    return place_name in _places_set() or bool(baked.flora_items(place_name))


def draw(
    place_name: str | None,
    seen: set[str],
    rng: random.Random,
    local_hour: int | None = None,
    country_code: str | None = None,
    intent: str | None = None,
    lat: float = 0.0,
    lon: float = 0.0,
    walk_step: int = 0,
    month: int | None = None,
    weekday: int | None = None,
) -> dict | None:
    """抽一张没见过的卡 {"category", "text", "key"};抽完或无此地 → None。

    两档级差: 手写层(五类卡)没抽空时,烘焙卡不进池("先吃现房,再吃罐头");
    手写层空了,烘焙卡才进池全权重顶上。饭点美食加权和级差叠乘。
    """
    if not place_name:
        return None

    # ── Card 54: thin-place speed limit ─────────────────────────────
    # Thin places (<5 effective cards): enforce _THIN_COOLDOWN steps
    # between draws, so the player doesn't exhaust a sparse place in
    # consecutive steps.  The gap is filled with sky/terrain/body/water/
    # phenology renderings in walk_impl.
    if is_thin_place(place_name):
        last = _thin_last_draw.get(place_name, -999)
        if walk_step - last < _THIN_COOLDOWN:
            return None

    # 候选池: (category, key, text, weight)
    pool: list[tuple[str, str, str, float]] = []

    # 手写层: filter Card objects by place and unseen
    handwritten_cards = [
        c for c in _load()
        if c.conditions.get("place") == place_name and c.id not in seen
    ]
    unseen_handwritten = len(handwritten_cards)
    has_local_food = False
    for c in handwritten_cards:
        cat = c.meta.get("category", "")
        w = c.meta.get("weight", 1.0)
        if not _conditions_pass(c, local_hour=local_hour, month=month, weekday=weekday):
            continue
        pool.append((cat, c.id, c.text, w))
        if cat == "美食":
            has_local_food = True

    # 级差: 手写层没空时烘焙卡不进池;空了才全权
    if unseen_handwritten == 0:
        # 只有本地没有特色食物时，才用国家级食物兜底
        if not has_local_food:
            for i, item in enumerate(baked.food_items(country_code, lat, lon, place_name=place_name)):
                key = f"{place_name}/烘焙美食/{i}"
                if key not in seen:
                    rendered = baked.render_food(item, rng)
                    if rendered is not None:
                        pool.append(("美食", key, rendered, 2.0))

        for i, item in enumerate(baked.flora_items(place_name)):
            key = f"{place_name}/烘焙植被/{i}"
            if key not in seen:
                pool.append(("植被", key, baked.render_flora(item, rng), 1.0))

    # Card 12: intent bias for food
    if intent in ("吃", "美食", "食物"):
        pool = [(cat, k, t, w * 2 if cat == "美食" else w) for cat, k, t, w in pool]

    if not pool:
        return None

    meal_time = local_hour is not None and (
        6 <= local_hour < 9 or 11 <= local_hour < 13 or 17 <= local_hour < 21
    )
    weights = [w if (meal_time or cat != "美食") else 1.0 for cat, _, _, w in pool]
    total = sum(weights)
    r = rng.uniform(0, total)
    result: dict | None = None
    for (cat, key, text, _), w in zip(pool, weights):
        r -= w
        if r <= 0:
            result = {"category": cat, "text": text, "key": key}
            break
    if result is None:
        cat, key, text, _ = pool[-1]
        result = {"category": cat, "text": text, "key": key}
    # ── Card 54: record draw step for thin-place cooldown ───────────
    _thin_last_draw[place_name] = walk_step
    return result


def rhythm_event(
    place_name: str | None,
    local_hour: int,
    rng: random.Random,
    month: int | None = None,
    recent: list[str] | None = None,
    weekday: int | None = None,
) -> str | None:
    """当前时刻命中的节律文案,没有 → None。

    卡可带 "months": [月份列表],带了就只在那些月出现(极昼/极光/
    三文鱼季这种季节限定);没带 = 全年有效。
    卡可带 "weekdays": [0-6], 0=周一, 6=周日;带了就只在那些天出现。
    recent: 最近出现过的文案, 跳过它们避免每步复读同一张卡。
    """
    if not place_name:
        return None

    # Filter cards: place + 节律 category
    hits: list[str] = []
    for c in _load():
        if c.conditions.get("place") != place_name:
            continue
        if c.meta.get("category") != "节律":
            continue

        if not _conditions_pass(c, local_hour=local_hour, month=month, weekday=weekday):
            continue

        hits.append(c.text)

    if not hits:
        return None
    recent_set = set(recent or [])
    if recent_set:
        fresh = [h for h in hits if h not in recent_set]
        if not fresh:
            # 当前时刻的卡全在 recent 里 → 安静比复读同一张卡好
            return None
        hits = fresh
    return rng.choice(hits)
