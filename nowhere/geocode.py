"""地理编码: Nominatim 在线 → GeoNames 离线兜底(买断数据)。

离线源: nowhere/data/packs/cities15000.txt(全球 1.5 万+ 城镇,
中文名在 alternatenames 列,按人口排序取最大)。
"""

from __future__ import annotations

import functools
import json
import pathlib
import urllib.parse

from nowhere.providers import _get_client

_PACK_PATH = pathlib.Path(__file__).resolve().parent / "data" / "packs" / "cities15000.txt"
_SPECIAL_PATH = pathlib.Path(__file__).resolve().parent / "data" / "special_places.json"

# Cache geocode results to avoid re-scanning places.db / cities15000 on every call
_geocode_cache: dict[str, tuple[float, float] | None] = {}
_special_places: dict[str, dict] | None = None


def clear_cache() -> None:
    """Clear the geocode cache (for testing)."""
    _geocode_cache.clear()


def _load_special() -> dict[str, dict]:
    """Load special_places.json once and cache."""
    global _special_places
    if _special_places is None:
        if _SPECIAL_PATH.exists():
            _special_places = json.loads(_SPECIAL_PATH.read_text(encoding="utf-8"))
        else:
            _special_places = {}
    return _special_places


def _offline_lookup(place: str) -> tuple[float, float] | None:
    """在 cities15000 里查地名。精确名 > 别名包含,同优先级取人口最多。"""
    if not _PACK_PATH.exists():
        return None
    q = place.strip().lower()
    if not q:
        return None

    best: tuple[float, float] | None = None
    best_score = -1.0
    with open(_PACK_PATH, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 15:
                continue
            name, ascii_name, alts = parts[1], parts[2], parts[3]
            score = 0.0
            if q == name.lower() or q == ascii_name.lower():
                score = 4.0
            elif q in name.lower() or q in ascii_name.lower():
                score = 2.0
            else:
                # 别名按 token 匹配: 整词相等 > 词内包含(防"喀什"撞上"马拉喀什")
                for token in alts.lower().split(","):
                    token = token.strip()
                    if not token:
                        continue
                    if token == q:
                        score = max(score, 3.0)
                        break
                    if q in token:
                        score = max(score, 1.0)
            if score == 0.0:
                continue
            try:
                pop = int(parts[14] or 0)
            except ValueError:
                pop = 0
            rank = score * 1e12 + pop
            if rank > best_score:
                best_score = rank
                best = (float(parts[4]), float(parts[5]))
    return best


async def lookup(place: str) -> tuple[float, float] | None:
    """Return ``(lat, lon)`` for *place*, or ``None`` on failure / no result.

    链: special_places → places.db → cities15000 → Nominatim（慢，最后试）。
    """
    key = place.strip().lower()
    if key in _geocode_cache:
        return _geocode_cache[key]

    # Special places (continents, oceans, poles, etc.)
    special = _load_special()
    if place in special:
        result = (special[place]["lat"], special[place]["lon"])
        _geocode_cache[key] = result
        return result

    # Offline sources first (fast)
    from nowhere import places

    hit = places.find(place)
    if hit is not None:
        result = (hit["lat"], hit["lon"])
        _geocode_cache[key] = result
        return result

    result = _offline_lookup(place)
    if result is not None:
        _geocode_cache[key] = result
        return result

    # Nominatim last (slow, 5s timeout)
    url = (
        "https://nominatim.openstreetmap.org/search?"
        + urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    )
    try:
        client = _get_client()
        resp = await client.get(url, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        if data:
            result = (float(data[0]["lat"]), float(data[0]["lon"]))
            _geocode_cache[key] = result
            return result
    except Exception:
        pass

    _geocode_cache[key] = None
    return None
