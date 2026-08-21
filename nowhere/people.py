"""卡中人——虚构的当地人,能遇见能搭话。

数据: people_seed.json,60人。
两层人的另一层(故人)由 humanities.py 的 人物 卡负责,本模块不碰。

遇见规则:
  walk 落在该地 5km 内 → 40% 出 sight (同一次旅程只出一次,再次路过 20% 不在)
  talk() → lines 轮换,第四句是"记得你"变体
  talk("路怎么走") → knows
  months 外不遇见
"""

from __future__ import annotations

import json
import math
import pathlib
import random
import sys

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
_SEED_FILE = _DATA_DIR / "people_seed.json"

_data: dict | None = None

_REMEMBER_VARIANTS: list[str] = [
    "又是你。坐。",
    "你又来了。茶还有。",
    "还是你。来。",
    "又来了?坐吧。",
]


def _load() -> dict:
    """Load people_seed.json once, cache globally."""
    global _data
    if _data is not None:
        return _data
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not _SEED_FILE.exists():
        print("[people] seed file missing", flush=True)
        _data = {}
        return _data
    _data = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    print(f"[people] loaded {len(_data)} people", flush=True)
    return _data


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine distance in km."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    d = min(d, 1.0)
    return 2 * 6371.0 * math.asin(math.sqrt(d))


# ── Place coords (seeded from people_seed places, keyed by place name) ──
# We need coords to do proximity matching.  The seed file doesn't carry coords;
# we look them up from humanities.json (which has lat/lon for most places)
# or from places_patch.json as fallback.

_place_coords: dict[str, tuple[float, float]] | None = None


def _load_place_coords() -> dict[str, tuple[float, float]]:
    """Build a place→coords map from humanities.json and places_patch.json."""
    global _place_coords
    if _place_coords is not None:
        return _place_coords
    coords: dict[str, tuple[float, float]] = {}

    # 1) humanities.json has lat/lon per place
    h_path = _DATA_DIR / "humanities.json"
    if h_path.exists():
        try:
            h = json.loads(h_path.read_text(encoding="utf-8"))
            for name, entry in h.get("places", {}).items():
                if "lat" in entry and "lon" in entry:
                    coords[name] = (entry["lat"], entry["lon"])
        except Exception:
            pass

    # 2) places_patch.json (flat {name: [lat, lon]} or {name: {lat,lon}})
    pp_path = _DATA_DIR / "places_patch.json"
    if pp_path.exists():
        try:
            pp = json.loads(pp_path.read_text(encoding="utf-8"))
            for name, val in pp.items():
                if name not in coords:
                    if isinstance(val, list) and len(val) >= 2:
                        coords[name] = (val[0], val[1])
                    elif isinstance(val, dict):
                        lat, lon = val.get("lat"), val.get("lon")
                        if lat is not None and lon is not None:
                            coords[name] = (lat, lon)
        except Exception:
            pass

    _place_coords = coords
    return _place_coords


def find_nearby_person(
    lat: float,
    lon: float,
    current_month: int,
    seen_people: set[str],
    rng: random.Random,
    radius_km: float = 5.0,
    force_encounter: bool = False,
) -> dict | None:
    """Walk 落在附近时,尝试遇见一个人。

    force_encounter=True 时跳过 40% 概率(测试用)。
    返回 {"person", "place", "sight", "where", "data"} 或 None。
    """
    data = _load()
    coords = _load_place_coords()
    here = (lat, lon)

    candidates: list[tuple[str, float]] = []
    for place in data:
        pc = coords.get(place)
        if pc is None:
            continue
        dist = _haversine_km(here, pc)
        if dist <= radius_km:
            candidates.append((place, dist))

    if not candidates:
        return None

    # Closest first
    candidates.sort(key=lambda x: x[1])

    for place, dist in candidates:
        entry = data[place]
        person = entry["person"]
        key = f"{place}/{person}"

        # months filter
        months = entry.get("months", [])
        if months and current_month not in months:
            continue

        # Already met this journey? 20% absent
        if key in seen_people:
            if not force_encounter and rng.random() < 0.20:
                continue  # absent on revisit
        else:
            # First encounter: 40% chance (skip if force_encounter)
            if not force_encounter and rng.random() >= 0.40:
                continue

        return {
            "person": person,
            "place": place,
            "sight": entry["sight"],
            "where": entry["where"],
            "data": entry,
        }

    return None


def talk(
    entry: dict,
    line_index: int,
    question: str | None = None,
    rng: random.Random | None = None,
) -> str:
    """返回一句搭话内容。

    question 含方向/路/怎么走/节日/传言/风声/传闻 → knows.text
    line_index >= len(lines) → 记得你变体
    """
    if question and any(k in question for k in (
        "路", "怎么走", "方向", "在哪", "哪里",
        "节日", "节", "传言", "风声", "传闻", "听说",
    )):
        knows = entry.get("knows", {})
        if knows and knows.get("text"):
            return knows["text"]

    lines = entry.get("lines", [])
    if line_index < len(lines):
        return lines[line_index]

    # 4th+ line: remember you variant
    if rng is None:
        rng = random.Random()
    return rng.choice(_REMEMBER_VARIANTS)
