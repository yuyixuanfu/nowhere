"""Find nearby radio stations via Radio-Browser API with fallback to a local JSON list."""

from __future__ import annotations

import json
import math
import pathlib
import random
from typing import Final

import httpx

from nowhere import country

# ── Constants ───────────────────────────────────────────────────────

_MIRRORS: Final[list[str]] = [
    "https://de1.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
    "https://at1.api.radio-browser.info",
]

_DATA_DIR: Final = pathlib.Path(__file__).resolve().parent / "data"
_FALLBACK_PATH: Final = _DATA_DIR / "radio_fallback.json"

_EARTH_RADIUS_KM: Final = 6371.0


# ── Helpers ─────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _load_fallback() -> list[dict]:
    try:
        with open(_FALLBACK_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _pick_nearest_from_fallback(lat: float, lon: float) -> dict | None:
    """Pick the fallback station closest to (lat, lon) by haversine distance.

    All fallback entries have ``lat``/``lon`` fields. If the nearest station
    is within 3000 km, return it. Otherwise, use a region-aware fallback:
    pick the closest representative station from the nearest region.

    Returns *None* only if no stations are available.
    """
    _MAX_NEARBY_KM: Final = 3000.0

    # Regional representative stations (picked from fallback list by country)
    _REGION_REPS: dict[str, list[str]] = {
        "asia":      ["KR", "JP", "CN", "VN", "TH", "ID", "IN", "KG"],
        "europe":    ["GB", "FR", "DE", "NO", "IS", "CZ"],
        "americas":  ["US", "CA", "BR", "AR", "PE"],
        "africa":    ["KE", "TZ", "ZA", "CM"],
        "oceania":   ["AU", "NZ", "FJ"],
        "mideast":   ["JO", "AE"],
    }

    stations = _load_fallback()
    if not stations:
        return None

    # Index stations by country code for regional lookup
    _by_cc: dict[str, list[dict]] = {}
    for st in stations:
        cc = st.get("country", "")
        if cc:
            _by_cc.setdefault(cc, []).append(st)

    # 1. Find globally nearest station
    best: dict | None = None
    best_dist = math.inf
    for st in stations:
        st_lat = st.get("lat")
        st_lon = st.get("lon")
        if st_lat is None or st_lon is None:
            continue
        d = _haversine_km(lat, lon, st_lat, st_lon)
        if d < best_dist:
            best_dist = d
            best = st

    if best is None:
        return None

    # If within 3000 km, return directly
    if best_dist <= _MAX_NEARBY_KM:
        return best

    # 2. Find nearest region by computing distance to each region's centroid
    _REGION_CENTROIDS: dict[str, tuple[float, float]] = {
        "asia":     (30.0, 105.0),
        "europe":   (50.0, 10.0),
        "americas": (15.0, -80.0),
        "africa":   (5.0, 25.0),
        "oceania":  (-25.0, 160.0),
        "mideast":  (30.0, 45.0),
    }

    nearest_region = None
    region_dist = math.inf
    for rname, (rlat, rlon) in _REGION_CENTROIDS.items():
        d = _haversine_km(lat, lon, rlat, rlon)
        if d < region_dist:
            region_dist = d
            nearest_region = rname

    if nearest_region is None:
        return best  # should not happen

    # 3. From that region, pick the nearest station to the user
    rep_ccs = _REGION_REPS.get(nearest_region, [])
    region_best: dict | None = None
    region_best_dist = math.inf
    for cc in rep_ccs:
        for st in _by_cc.get(cc, []):
            st_lat = st.get("lat")
            st_lon = st.get("lon")
            if st_lat is None or st_lon is None:
                continue
            d = _haversine_km(lat, lon, st_lat, st_lon)
            if d < region_best_dist:
                region_best_dist = d
                region_best = st

    return region_best or best


# ── Public API ──────────────────────────────────────────────────────

async def nearest(lat: float, lon: float, country_code: str | None) -> dict | None:
    """Return a station dict ``{name, genre, stream_url, homepage}`` or ``None``.

    离线优先：先查本地兜底清单，再试外网 API。
    """
    # ── 1. Offline fallback first (instant) ─────────────────────────
    fallback = _pick_nearest_from_fallback(lat, lon)
    if fallback is not None:
        return fallback

    # ── 2. Try live API (slow) ──────────────────────────────────────
    if country_code is None:
        country_code = country.country_code_of(lat, lon)

    mirrors = list(_MIRRORS)
    random.shuffle(mirrors)

    _EXCLUDED_TAGS = {"game", "gaming", "gamemusic", "video game", "esports"}

    def _is_excluded(station: dict) -> bool:
        tags = (station.get("tags") or "").lower()
        return any(excl in tags for excl in _EXCLUDED_TAGS)

    if country_code:
        async with httpx.AsyncClient(timeout=8.0) as client:
            for base in mirrors:
                try:
                    url = (
                        f"{base}/json/stations/search"
                        f"?countrycode={country_code}&limit=50"
                        f"&order=clickcount&has_geo_info=true"
                    )
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                    if data:
                        # Filter out gaming/non-music stations
                        data = [st for st in data if not _is_excluded(st)]
                        if not data:
                            continue  # try next mirror or fallback
                        # 有坐标的台里挑地理最近的,都不带坐标就用最热的一个
                        best = None
                        best_d = math.inf
                        for st in data:
                            glat, glon = st.get("geo_lat"), st.get("geo_long")
                            if glat is None or glon is None:
                                continue
                            d = _haversine_km(lat, lon, glat, glon)
                            if d < best_d:
                                best_d = d
                                best = st
                        st = best or data[0]
                        return {
                            "name": st.get("name", "Unknown"),
                            "genre": st.get("tags", ""),
                            "stream_url": st.get("url_resolved", st.get("url", "")),
                            "homepage": st.get("homepage", ""),
                        }
                except (httpx.HTTPError, httpx.TimeoutException):
                    continue

    # ── Fallback ─────────────────────────────────────────────────────
    return _pick_nearest_from_fallback(lat, lon)
