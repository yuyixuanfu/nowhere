"""Nowhere MCP server -- wires all modules into 8 tools.

Usage:
    python -m nowhere.server          # stdio MCP server
    python -m nowhere.server --web 8080  # reserved for Task 11
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import threading
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from timezonefinder import TimezoneFinder

from fastmcp import FastMCP

from nowhere import (
    art,
    country,
    describe,
    encounters,
    geocode,
    hydrology,
    humanities,
    knowledge,
    landing,
    life,
    listen as listen_mod,
    localcolor,
    marks as marks_mod,
    placememory,
    places,
    poster,
    providers,
    radio,
    salience,
    sky,
    soundscape,
    state as state_mod,
    terrain,
    walk as walk_mod,
    water,
    weather,
)

mcp = FastMCP("nowhere")

# ── Module-level state ───────────────────────────────────────────────

_state: state_mod.WorldState = state_mod.WorldState()
_door_lock = asyncio.Lock()  # open_door 竞态保护:一次只开一扇门
_postcard_counter: int = 0  # 跨门的明信片编号,不走 state 重置
_rng: random.Random = (
    random.Random(int(os.environ["NOWHERE_SEED"]))
    if os.environ.get("NOWHERE_SEED")
    else random.Random()  # 生产真随机;测试用 NOWHERE_SEED 锁
)
_web_port: int | None = None  # reserved for Task 11
_tf: TimezoneFinder = TimezoneFinder()
_recent_salience_kinds: set[str] = set()  # Bug 4: track recent salience kinds

# ── Bearing mapping ──────────────────────────────────────────────────

_BEARING_MAP: dict[str, float] = {
    "N": 0, "NE": 45, "E": 90, "SE": 135,
    "S": 180, "SW": 225, "W": 270, "NW": 315,
    "北": 0, "东北": 45, "东": 90, "东南": 135,
    "南": 180, "西南": 225, "西": 270, "西北": 315,
}

_SEMANTIC_MAP: dict[str, str] = {
    "uphill": "uphill", "toward_sea": "toward_sea", "forward": "forward",
    "上山": "uphill", "向海": "toward_sea", "向前": "forward",
    "上坡": "uphill", "下海": "toward_sea",
}

# ── Quiet variants for look_around ───────────────────────────────────

_QUIET_VARIANTS: list[str] = [
    "周围安静。",
    "四下无人,只有风声。",
    "安静得能听到自己的心跳。",
    "什么声音也没有。世界好像只剩你一个。",
    "这里没有路,也没有人走过的痕迹。",
]

# 留白: 缓存命中且世界没变时的回话——路就是路
_QUIET_WALK = [
    "路就是路。你往前走。",
    "什么也没发生。这也算一种发生。",
    "世界没有更新。",
    "风还是那阵风。",
    "你走你的,世界忙它的。",
    "脚下的路和刚才一样。",
]
_QUIET_WAIT = [
    "时间过去了。光没变。",
    "什么都没变,只有时间变了。",
]


# =====================================================================
# Helpers
# =====================================================================


def _load_scene_file(filename: str) -> dict[str, list[str]]:
    """Load a [城市名] 描述 format file into {city: [descriptions]} dict."""
    cache_key = f"_scene_{filename}"
    if not hasattr(_load_scene_file, cache_key):
        result: dict[str, list[str]] = {}
        fp = describe._SCENE_DIR / f"{filename}.txt"
        if fp.exists():
            for line in fp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "] " in line:
                    bracket_end = line.index("] ")
                    place = line[1:bracket_end]
                    desc = line[bracket_end + 2:]
                    result.setdefault(place, []).append(desc)
        setattr(_load_scene_file, cache_key, result)
    return getattr(_load_scene_file, cache_key)


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Quick equirectangular distance, good enough for station stickiness."""
    import math

    dlat = math.radians(a[0] - b[0])
    dlon = math.radians(a[1] - b[1]) * math.cos(math.radians(a[0]))
    return 6371.0 * math.sqrt(dlat * dlat + dlon * dlon)


def _last_env_surface() -> str:
    """Read ``surface`` from ``_state.last_env`` regardless of write format.

    Two writers exist:
    * ``_gather_env_cached`` writes top-level: ``{elevation, surface, ...}``
    * ``walk_impl`` / ``look_around_impl`` / ``wait_impl`` write nested:
      ``{terrain: {elevation, surface}, ...}``

    Callers used to read only the nested path; when last_env came from the
    cache miss they got ``""``.  This helper returns whichever shape was used.
    """
    env = _state.last_env or {}
    nested = env.get("terrain")
    if isinstance(nested, dict) and "surface" in nested:
        return nested["surface"]
    return env.get("surface", "")


def _last_env_terrain_dict() -> dict:
    """Return ``_state.last_env['terrain']`` (or a synthesized equivalent).

    When ``last_env`` is in the top-level shape (``{elevation, surface}``),
    wrap it as ``{elevation, surface}`` so ``salience`` callers that read
    ``prev["terrain"]["elevation"]`` keep working.
    """
    env = _state.last_env or {}
    nested = env.get("terrain")
    if isinstance(nested, dict):
        return nested
    # Top-level shape — synthesize a terrain dict.
    out: dict = {}
    if "elevation" in env:
        out["elevation"] = env["elevation"]
    if "surface" in env:
        out["surface"] = env["surface"]
    return out


async def _get_radio(lat: float, lon: float) -> dict | None:
    """Sticky radio: reuse the station if we haven't drifted 50km from
    where it was picked. 同一个地方就该是同一个台。"""
    if _state.radio_station is not None and _state.radio_pos is not None:
        if _km((lat, lon), _state.radio_pos) < 50.0:
            return _state.radio_station
    cc = country.country_code_of(lat, lon)
    station = await radio.nearest(lat, lon, cc)
    if station is not None:
        _state.radio_station = station
        _state.radio_pos = (lat, lon)
    return station


def _parse_bearing(direction: str) -> tuple[float | None, str | None, bool]:
    """Parse direction string into ``(bearing_deg, semantic, invalid)``.

    ``invalid`` is True when the input could not be recognised and was
    silently replaced with "forward".
    """
    d = direction.strip()
    upper = d.upper()
    if upper in _BEARING_MAP:
        return _BEARING_MAP[upper], None, False
    if d in _BEARING_MAP:
        return _BEARING_MAP[d], None, False
    if d in _SEMANTIC_MAP:
        return None, _SEMANTIC_MAP[d], False
    return None, "forward", True


# ── Nearby destinations hint ────────────────────────────────────────

_DEST_TEMPLATES: list[str] = [
    "风从{dir}吹来,那边有{place}。",
    "{dir}方有什么在等着,{place}不远了。",
    "空气里隐约有{place}的方向,往{dir}走试试。",
    "脚下这条路通往{place},就在{dir}边。",
    "{dir}边的地平线上,{place}的轮廓若隐若现。",
    "远处{dir}方,{place}像一个还没讲完的故事。",
]


def _find_nearby_destinations(lat: float, lon: float, rng) -> str:
    """Return a literary hint about a walkable place within ~20km."""
    import json
    import pathlib as _pathlib
    from math import radians, sin, cos, sqrt, atan2

    def _haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return R * 2 * atan2(sqrt(a), sqrt(1 - a))

    patch_path = _pathlib.Path(__file__).resolve().parent / "data" / "places_patch.json"
    if not patch_path.exists():
        return ""
    try:
        places = json.loads(patch_path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    nearby = []
    for name, coords in places.items():
        if isinstance(coords, dict):
            plat, plon = coords.get("lat"), coords.get("lon")
        elif isinstance(coords, list) and len(coords) >= 2:
            plat, plon = coords[0], coords[1]
        else:
            continue
        if plat is None or plon is None:
            continue
        d = _haversine_km(lat, lon, plat, plon)
        if 0.5 < d <= 20:
            nearby.append((name, d, plat, plon))

    if not nearby:
        return ""

    nearby.sort(key=lambda x: x[1])
    name, d, plat, plon = rng.choice(nearby[:3])

    # 算方位
    import math
    bearing = math.degrees(math.atan2(
        math.radians(plon - lon), math.radians(plat - lat)
    )) % 360
    dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    direction = dirs[int((bearing + 22.5) / 45) % 8]

    template = rng.choice(_DEST_TEMPLATES)
    return template.format(place=name, dir=direction)


# ── Water feature nearest-point lookup ──────────────────────────────

def _find_nearest_water_feature(name: str, lat: float, lon: float) -> dict | None:
    """Find the nearest point on a named water feature from the offline database."""
    import json
    import pathlib as _pathlib

    fp = _pathlib.Path(__file__).resolve().parent / "data" / "water_features_offline.json"
    if not fp.exists():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None

    entries = data.get("entries", [])
    best = None
    best_dist = float("inf")

    for entry in entries:
        entry_name = entry.get("name", "")
        # 名称匹配（包含关系）
        if name not in entry_name and entry_name not in name:
            continue
        elat, elon = entry.get("lat", 0), entry.get("lon", 0)
        radius = entry.get("radius_km", 50)
        # 简化距离：用条目中心点距离减去半径（近似最近距离）
        d = places._haversine_km(lat, lon, elat, elon)
        d_approx = max(0, d - radius)
        if d_approx < best_dist:
            best_dist = d_approx
            # 用当前坐标和条目中心的连线上的点作为最近点（简化）
            if d > 0:
                ratio = min(radius / d, 1.0)
                near_lat = lat + (elat - lat) * ratio
                near_lon = lon + (elon - lon) * ratio
            else:
                near_lat, near_lon = elat, elon
            best = {"lat": near_lat, "lon": near_lon, "type": entry.get("type", "水域")}

    return best


# ── Walk discovery system ───────────────────────────────────────────

_DISCOVERY_CACHE: list[str] | None = None

_SURFACE_DESC_SERVER: dict[str, str] = {
    "rock": "岩石",
    "sand": "沙",
    "snow": "积雪",
    "ice": "冰面",
    "forest": "林地",
    "grass": "草地",
    "urban": "硬化路面",
    "bare": "碎石",
    "wetland": "湿地",
    "water_ocean": "海面",
    "water_fresh": "水面",
}


def _load_discovery_scenes() -> list[str]:
    """Load walk discovery scenes from scene_walk_discovery.txt."""
    global _DISCOVERY_CACHE
    if _DISCOVERY_CACHE is None:
        fp = describe._SCENE_DIR / "scene_walk_discovery.txt"
        if fp.exists():
            lines = [l.strip() for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
            _DISCOVERY_CACHE = lines
        else:
            _DISCOVERY_CACHE = []
    return _DISCOVERY_CACHE


def _terrain_transition_text(
    last_surface: str | None, current_surface: str, rng: random.Random
) -> str:
    """Describe the transition between two surface types."""
    if not last_surface or last_surface == current_surface:
        return ""
    last_desc = _SURFACE_DESC_SERVER.get(last_surface, last_surface)
    curr_desc = _SURFACE_DESC_SERVER.get(current_surface, current_surface)
    transitions = [
        f"地面从{last_desc}变成了{curr_desc}。",
        f"脚下的{last_desc}不见了，现在是{curr_desc}。",
        f"从{last_desc}走到了{curr_desc}上。",
        f"路变了，{last_desc}换成了{curr_desc}。",
    ]
    return rng.choice(transitions)


def _pick_discovery(rng: random.Random) -> str:
    """Pick a random discovery scene line, filtered by biome."""
    pool = _load_discovery_scenes()
    if not pool:
        return ""

    # Filter out scenes that don't match the current biome
    biome = _state.biome or ""
    # last_env may be nested ({terrain:{surface}}) or top-level ({surface});
    # use the helper so both shapes work.
    surface = _last_env_surface()

    # Water scenes are inappropriate in deserts and dry areas
    water_keywords = ["瀑布", "溪", "河", "湖", "海", "水帘", "湿地", "溪水"]
    if biome in ("desert",) or surface in ("sand", "bare"):
        pool = [s for s in pool if not any(k in s for k in water_keywords)]

    # Ice/snow scenes are inappropriate in hot/desert areas
    ice_keywords = ["冰", "雪", "冻", "霜", "冰湖", "冰面"]
    if biome in ("desert", "rainforest") or surface in ("sand", "bare"):
        pool = [s for s in pool if not any(k in s for k in ice_keywords)]

    # Sea scenes are inappropriate in landlocked areas
    if "海" in "".join(pool) and biome not in ("coast",):
        # Check if we're far from the sea (simplified: >100km from coast)
        # For now, just filter out explicit sea scenes for non-coast biomes
        if biome not in ("coast", "island"):
            pool = [s for s in pool if "海边" not in s and "灯塔" not in s]

    if not pool:
        return ""
    return rng.choice(pool)


# ── Narrative continuity system ──────────────────────────────────────

_DIRECTION_LABELS: dict[float, str] = {
    0: "北", 45: "东北", 90: "东", 135: "东南",
    180: "南", 225: "西南", 270: "西", 315: "西北",
}

_TIME_FLOW_LINES: list[str] = [
    "太阳往西移了一点。",
    "天色暗了一些。",
    "影子变长了。",
    "风向变了。",
    "云层厚了一些。",
    "光线柔和了下来。",
]

_BODY_STATE_LINES: list[str] = [
    "你的嘴唇上有一层盐。",
    "你开始出汗了。",
    "你的腿有点酸。",
    "你深吸了一口气。",
    "你舔了一下嘴唇，干的。",
    "你的脚底有点疼。",
    "你擦了一下额头上的汗。",
]


def _bearing_to_label(bearing_deg: float | None, semantic: str | None) -> str | None:
    """Convert bearing degrees or semantic direction to a Chinese label."""
    if bearing_deg is not None:
        key = round(bearing_deg / 45) * 45 % 360
        return _DIRECTION_LABELS.get(key)
    if semantic == "uphill":
        return "上山"
    if semantic == "toward_sea":
        return "海边"
    return None


def _build_walk_narrative(
    step_result: dict,
    env: dict,
    bearing_deg: float | None,
    semantic: str | None,
    rng: random.Random,
) -> str:
    """Build a continuous narrative opener for this walk step.

    Reads and updates ``_state.narrative`` to produce text that connects
    this step to the previous one, instead of independent fragments.
    """
    parts: list[str] = []
    narrative = _state.narrative

    # ── 1. Direction ──────────────────────────────────────────────────
    new_dir = _bearing_to_label(bearing_deg, semantic)
    if new_dir and new_dir != narrative.get("direction"):
        if narrative.get("direction"):
            parts.append(f"你转身往{new_dir}走。")
        else:
            parts.append(f"你往{new_dir}走了几步。")
        narrative["direction"] = new_dir
        narrative["distance_walked"] = 0
    elif new_dir and not narrative.get("direction"):
        narrative["direction"] = new_dir

    # ── 2. Terrain transition ─────────────────────────────────────────
    prev_surface = _state.last_surface
    curr_surface = step_result.get("new_surface", env.get("surface", ""))
    if prev_surface and prev_surface != curr_surface:
        last_desc = _SURFACE_DESC_SERVER.get(prev_surface, prev_surface)
        curr_desc = _SURFACE_DESC_SERVER.get(curr_surface, curr_surface)
        slope = step_result.get("slope_deg", 0)
        if slope > 15:
            parts.append(f"路开始爬升，地面从{last_desc}变成了{curr_desc}。")
        else:
            parts.append(f"地面从{last_desc}变成了{curr_desc}。")

    # ── 3. Distance ───────────────────────────────────────────────────
    dist_km = step_result.get("dist_km", 2.0)
    narrative["distance_walked"] += dist_km * 1000
    walked = narrative["distance_walked"]
    if walked > 10000:
        parts.append(f"你已经走了{walked / 1000:.0f}公里了。")
        narrative["distance_walked"] = 0
    elif walked > 5000 and rng.random() < 0.3:
        parts.append(f"又走了{dist_km:.1f}公里。")
        narrative["distance_walked"] = 0

    # ── 4. Discovery ──────────────────────────────────────────────────
    if _state.steps_since_discovery >= 2 and rng.random() < 0.4:
        disc = _pick_discovery(rng)
        if disc:
            parts.append(disc)
            narrative["discoveries"].append(disc[:20])
            narrative["last_feature"] = disc[:20]
            # Reset so the next discovery waits another 2+ steps; without this
            # reset the counter only ever grows and discovery fires once.
            _state.steps_since_discovery = 0

    # ── 5. Time flow ──────────────────────────────────────────────────
    if rng.random() < 0.3:
        parts.append(rng.choice(_TIME_FLOW_LINES))

    # ── 6. Body state ─────────────────────────────────────────────────
    if rng.random() < 0.2:
        parts.append(rng.choice(_BODY_STATE_LINES))

    return "".join(parts)


async def _gather_env(lat: float, lon: float, dt: datetime) -> dict[str, Any]:
    """Gather weather / sky / terrain / radio for a position.

    Uses ``asyncio.gather`` with ``return_exceptions=True`` so one failure
    does not block the others.
    """
    # Elevation fetched first so weather can use lapse rate correction
    elev_result = await asyncio.to_thread(terrain.elevation, lat, lon)
    elev: float = elev_result if not isinstance(elev_result, Exception) else 0.0

    # Get local hour for diurnal temperature variation
    local_hour = None
    if dt:
        tz_name = _tf.timezone_at(lat=lat, lng=lon)
        if tz_name:
            local_dt = dt.astimezone(ZoneInfo(tz_name))
            local_hour = local_dt.hour

    tasks: list[Any] = [
        asyncio.to_thread(terrain.surface, lat, lon),
        asyncio.to_thread(sky.sun_moon, lat, lon, dt),
        asyncio.to_thread(sky.visible_sky, lat, lon, dt, _rng),
        weather.current(lat, lon, elevation=elev, local_hour=local_hour),
        _get_radio(lat, lon),
        asyncio.wait_for(hydrology.nearby_water(lat, lon), timeout=5.0),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    def _ok(i: int, default: Any = None) -> Any:
        return results[i] if not isinstance(results[i], Exception) else default

    surf: str = _ok(0, "unknown")
    sun_moon_info: dict = _ok(1, {})
    visible_sky_info: dict = _ok(2, {})
    weather_info: dict = _ok(3, {})
    radio_info: dict | None = _ok(4, None)
    water_features: list[dict] = _ok(5, [])

    sky_info: dict = {**sun_moon_info, **visible_sky_info}

    return {
        "elevation": elev,
        "surface": surf,
        "sky": sky_info,
        "weather": weather_info,
        "radio": radio_info,
        "water_features": water_features,
    }


# env 惯性: 3km/30min 内,风还是那个风
_ENV_CACHE_KM = 3.0
_ENV_CACHE_MIN = 30


async def _gather_env_cached(lat: float, lon: float, dt: datetime) -> tuple[dict, bool]:
    """3km/30min 内复用上次 env。返回 (env, 缓存命中?)。"""
    if (
        _state.last_env is not None
        and _state.env_pos is not None
        and _state.env_at is not None
        and dt is not None
        and _km(_state.env_pos, (lat, lon)) < _ENV_CACHE_KM
        and abs((dt - _state.env_at).total_seconds()) < _ENV_CACHE_MIN * 60
    ):
        return _state.last_env, True
    env = await _gather_env(lat, lon, dt)
    _state.last_env = env
    _state.env_pos = (lat, lon)
    if dt is not None:
        _state.env_at = dt
    return env, False


# ── Salience delta helpers ───────────────────────────────────────────


def _weather_delta(old: dict | None, new: dict) -> float:
    if not old:
        return 1.0
    d_temp = abs(new.get("temp_c", 0) - old.get("temp_c", 0)) / 20.0
    d_wind = abs(new.get("wind_ms", 0) - old.get("wind_ms", 0)) / 15.0
    return min(1.0, d_temp + d_wind)


def _terrain_delta(old: dict | None, new: dict) -> float:
    if not old:
        return 1.0
    return min(1.0, abs(new.get("elevation", 0) - old.get("elevation", 0)) / 500.0)


def _sky_delta(old: dict | None, new: dict) -> float:
    if not old:
        return 1.0
    old_phase = old.get("phase", "day")
    new_phase = new.get("phase", "day")
    # phase switch (day <-> night) counts as full delta
    if (old_phase == "day") != (new_phase == "day"):
        return 1.0
    return 0.0


def _build_salience_candidates(
    env: dict[str, Any],
    prev_env: dict[str, Any] | None,
) -> list[dict]:
    """Build salience candidate list from environment data."""
    candidates: list[dict] = []

    # weather
    w = env.get("weather", {})
    if w:
        candidates.append({
            "kind": "weather",
            "delta": _weather_delta((prev_env or {}).get("weather"), w),
            "novelty": 0.2,
            "body_distance": 0.1,
            "payload": w,
        })

    # terrain
    t = {
        "surface": env.get("surface", "unknown"),
        "elevation": env.get("elevation", 0),
        "slope_deg": env.get("slope_deg", 0),
        "elevation_delta": env.get("elevation_delta", 0),
    }
    candidates.append({
        "kind": "terrain",
        "delta": _terrain_delta((prev_env or {}).get("terrain"), t),
        "novelty": 0.2,
        "body_distance": 0.1,
        "payload": t,
    })

    # sky
    s = env.get("sky", {})
    if s:
        candidates.append({
            "kind": "sky",
            "delta": _sky_delta((prev_env or {}).get("sky"), s),
            "novelty": 0.2,
            "body_distance": 0.7,
            "payload": s,
        })

    # radio (optional)
    r = env.get("radio")
    if r:
        candidates.append({
            "kind": "radio",
            "delta": 1.0,
            "novelty": 0.4,
            "body_distance": 0.6,
            "payload": r,
        })

    # water features (optional)
    wf = env.get("water_features")
    if wf:
        candidates.append({
            "kind": "water_features",
            "delta": 1.0,
            "novelty": 0.5,
            "body_distance": 0.3,
            "payload": wf,
        })

    return candidates


# =====================================================================
# Tool implementations (_impl) -- testable without MCP protocol
# =====================================================================


async def open_door_impl(to: str | None = None, resume: bool = False) -> dict:
    """Open the door and land somewhere."""
    async with _door_lock:
        return await _open_door_locked(to, resume=resume)


async def _open_door_locked(to: str | None = None, resume: bool = False) -> dict:
    """Door body, called under _door_lock."""
    global _state, _rng, _recent_salience_kinds

    # ── 1. Locate ────────────────────────────────────────────────────
    spot: dict | None = None
    if to is None:
        spot = landing.random_spot(_rng)
        lat, lon = spot["lat"], spot["lon"]
        place_name: str = spot.get("name_hint", "未知之地")
    else:
        mark_entry = marks_mod.get(to)
        if mark_entry:
            lat, lon = mark_entry["lat"], mark_entry["lon"]
            place_name = to
        else:
            # Check humanities.json for matching place name
            h_place = humanities.get_place_coords(to)
            if h_place:
                lat, lon = h_place["lat"], h_place["lon"]
                place_name = to
            else:
                result = await geocode.lookup(to)
                if result is None:
                    return {"text": f"找不到「{to}」。", "data": {"error": "not_found"}}
                lat, lon = result
                place_name = to

    # ── 2. State init ────────────────────────────────────────────────
    if resume:
        # Explicit resume: load saved journey if it exists
        saved = state_mod.WorldState.load()
        if saved and saved.pos is not None:
            _state = saved
            # Restore postcard counter to avoid ID collisions
            global _postcard_counter
            _postcard_counter = max((c.get("id", 0) for c in _state.postcards), default=0)
            lat, lon = _state.pos
            place_name = _state.place_name or "未知之地"
        else:
            _state = state_mod.WorldState()
            _state.pos = (lat, lon)
            _state.landed_at = datetime.now(timezone.utc)
            _state.place_name = place_name
            _state.biome = spot.get("biome") if spot else None
    else:
        # Fresh landing (random or named destination): always reset state
        # Preserve seen sets to avoid re-triggering the same cards
        old_seen_cards = _state.seen_cards.copy() if _state else set()
        old_seen_humanities = _state.seen_humanities.copy() if _state else set()
        _state = state_mod.WorldState()
        _state.pos = (lat, lon)
        _state.landed_at = datetime.now(timezone.utc)
        _state.place_name = place_name
        _state.biome = spot.get("biome") if spot else None
        _state.seen_cards = old_seen_cards
        _state.seen_humanities = old_seen_humanities
    # 地方记忆: 这地方记得你
    _state.seen_cards = placememory.seen_cards(place_name)
    _state.seen_humanities = placememory.seen_humanities()
    # 旅程内计数: fresh journey starts at 1, resume continues journey-local count
    visit_no = _state.record_journey_visit(place_name)
    # Also record to global placememory for historical tracking
    placememory.record_visit(place_name)

    # ── 3. Gather metadata ───────────────────────────────────────────
    env, _ = await _gather_env_cached(lat, lon, _state.now())
    placememory.record_landing(
        place_name, lat, lon,
        elevation=env.get("elevation"), surface=env.get("surface"),
    )

    # biome 缺失时按地表推(定向开门没有 pool 标签)
    if _state.biome is None:
        _SURFACE_BIOME = {
            "urban": "city", "water_ocean": "coast", "water_fresh": "coast",
            "forest": "rainforest", "sand": "desert", "bare": "desert",
            "snow": "tundra", "ice": "tundra", "rock": "mountain", "grass": "grassland",
        }
        _state.biome = _SURFACE_BIOME.get(env.get("surface", ""), None)

    # ── 3.5. Water features + SST + marine life ──────────────────────
    water_text = ""
    # Skip hydrology for now (Overpass API blocked in China)
    water_features = []

    # Sea surface temperature
    sst_text = ""
    try:
        sst = await water.sea_surface_temp(lat, lon)
        if sst is not None:
            sst_text = water.describe_sst(sst, _rng)
    except Exception:
        pass

    # Marine life encounter (30% chance near water)
    marine_text = ""
    if _rng.random() < 0.3:
        try:
            m = await water.marine_life(lat, lon, _rng)
            if m:
                marine_text = f"{m['common_name']}。{m['distance_m']}米外。{m['scene']}"
        except Exception:
            pass

    # ── 4. Salience candidates → rank ────────────────────────────────
    candidates = _build_salience_candidates(env, None)
    top3 = salience.rank(candidates, _rng, recent_kinds=_recent_salience_kinds)
    _recent_salience_kinds = {c["kind"] for c in top3}

    # ── 5. 开幕镜头 + top3(天气/天空已被开幕吃掉)─────────────────────
    sound = soundscape.describe_sound(
        {
            "weather": env.get("weather") or {},
            "sky": env.get("sky") or {},
            "surface": env.get("surface", ""),
            "mode": _state.mode,
        },
        _rng,
    )
    # 钩子从数据来: 电台/能爬的高处/水边/附近地标
    hooks: list[tuple[str, str | None]] = []
    if env.get("radio"):
        hooks.append(("radio", None))
    if env.get("surface") in ("water_ocean", "water_fresh") or _state.mode == "water":
        hooks.append(("water", None))
    try:
        gains = walk_mod.best_uphill_gain(_state)
        if gains and gains > 50:
            hooks.append(("uphill", None))
    except AttributeError:
        pass

    # 附近可去的地方——单独传，不跟其他钩子竞争
    nearby_places = _find_nearby_destinations(lat, lon, _rng)
    local_hour = None
    cc = None
    tz_name = _tf.timezone_at(lat=lat, lng=lon)
    if tz_name and _state.now() is not None:
        local_hour = _state.now().astimezone(ZoneInfo(tz_name)).hour
    cc = country.country_code_of(lat, lon)
    _now = _state.now()
    establish = describe.render_establish(
        {
            "place": place_name,
            "country_code": cc,
            "phase": env["sky"].get("phase", "day"),
            "local_hour": local_hour,
            "surface": env.get("surface", "grass"),
            "weather": env.get("weather"),
            "sound": sound,
            "hooks": hooks,
            "nearby_places": nearby_places,
            "biome": _state.biome or "",
            "elevation": env.get("elevation", 0),
            "lat": lat,
            "lon": lon,
            "month": _now.month if _now else 7,
        },
        _rng,
    )
    sections: list[str] = [establish]
    if visit_no > 1:
        sections[0] = f"又来了——第 {visit_no} 次来{place_name}。" + establish
    for c in top3:
        if c["kind"] in ("weather", "sky", "arrive"):
            continue
        text = describe.render(c["kind"], c["payload"], None, _rng,
                               biome=_state.biome or "", elevation=env.get("elevation", 0))
        if text:
            sections.append(text)

    if water_text:
        sections.append(water_text)
    if sst_text:
        sections.append(sst_text)
    if marine_text:
        sections.append(marine_text)

    prose = describe.compose(sections, _rng)
    _now = _state.now()
    _month = _now.month if _now else None
    prose = describe.sanity_check(prose, {**env, "_season": describe._season(_month, lat) if _month else ""})

    # ── 5d. 人文卡: 落点附近触发 ─────────────────────────────────
    h_card = humanities.nearby_place(lat, lon, _state.seen_humanities, _rng)
    if h_card:
        _state.seen_humanities.add(h_card["key"])
        placememory.save_seen_humanities(_state.seen_humanities)
        excerpt = h_card["text"][:60] + ("..." if len(h_card["text"]) > 60 else "")
        prose += f"你落在了{h_card['place']}附近。这里有过——{excerpt}"

    _state.last_text = prose
    _state.save()

    # ── 6. Save env snapshot ─────────────────────────────────────────
    _state.last_env = {
        "weather": env.get("weather"),
        "terrain": {
            "elevation": env.get("elevation"),
            "surface": env.get("surface"),
        },
        "sky": env.get("sky"),
    }

    # ── 7. Return ────────────────────────────────────────────────────
    return {
        "text": prose,
        "data": {
            "position": {"lat": lat, "lon": lon},
            "biome": spot.get("biome") if spot else None,
            "weather": env.get("weather"),
            "sky": env.get("sky"),
            "radio": env.get("radio"),
            "surface": env.get("surface"),
            "elevation": env.get("elevation"),
        },
    }


# ── Souvenir: natural pickup ────────────────────────────────────────

_SOUVENIR_TEMPLATES: dict[str, list[dict]] = {
    "desert": [
        {"name": "一块风蚀石", "desc": "你捡了一块石头，风把它磨得光滑。你把它揣进口袋。"},
        {"name": "一粒沙", "desc": "沙子钻进了鞋里。你倒出来，攥在手心，没扔。"},
    ],
    "forest": [
        {"name": "一片落叶", "desc": "地上有一片叶子，脉络清楚得像地图。你把它夹在手指间。"},
        {"name": "一截枯枝", "desc": "你捡了一截枯枝，树皮已经掉了，木头是温的。"},
    ],
    "mountain": [
        {"name": "一块碎石", "desc": "碎石里有一块，断面闪着光。你把它放进口袋。"},
        {"name": "一片冰碴", "desc": "你从冰面上掰了一小块，攥在手里，凉得发麻。它在慢慢变小。"},
    ],
    "water": [
        {"name": "一瓶江水", "desc": "你蹲下来，用手捧了一捧水，装进瓶子里。水是浑的，有泥沙的味道。"},
        {"name": "一枚贝壳", "desc": "沙子里露出半枚贝壳，边缘已经被磨圆了。你把它捡起来。"},
    ],
    "snow": [
        {"name": "一片雪花", "desc": "你伸出手，一片雪花落在掌心。还没来得及看清就化了。你又接了一片。"},
        {"name": "一块冰", "desc": "你从冰面上敲了一小块，透明的，里面有气泡。"},
    ],
    "urban": [
        {"name": "一张车票", "desc": "地上有一张用过的车票。你看了一眼日期，揣进口袋。"},
        {"name": "一颗扣子", "desc": "路边有一颗扣子，不知道是谁掉的。你捡起来看了看，又放下了，最后还是揣进口袋。"},
    ],
    "volcano": [
        {"name": "一块火山石", "desc": "黑色的火山石，轻得不像石头。表面全是气孔。你把它装进口袋。"},
    ],
    "grassland": [
        {"name": "一株草", "desc": "你拔了一株草，根上还带着土。草的味道是苦的。"},
    ],
    "tundra": [
        {"name": "一块苔藓", "desc": "苔藓从石头上剥下来，绿得发黑。湿的，软的。你把它包在纸里。"},
    ],
}


_SOUVENIRS_BY_PLACE: dict | None = None


def _load_souvenirs_by_place() -> dict:
    """Load souvenirs_by_place.json once and cache."""
    global _SOUVENIRS_BY_PLACE
    if _SOUVENIRS_BY_PLACE is None:
        import json as _json
        import pathlib as _pathlib
        fp = _pathlib.Path(__file__).resolve().parent / "data" / "souvenirs_by_place.json"
        _SOUVENIRS_BY_PLACE = _json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    return _SOUVENIRS_BY_PLACE


def _pick_souvenir(lat: float, lon: float, env: dict, rng: random.Random) -> dict | None:
    """Pick a natural souvenir based on current terrain/biome.

    Place-specific souvenirs (souvenirs_by_place.json) take priority over
    generic biome-based souvenirs.
    """
    place = _state.place_name or ""

    # 1. Try place-specific souvenirs first
    if place:
        place_souvenirs = _load_souvenirs_by_place().get(place)
        if place_souvenirs:
            item = rng.choice(place_souvenirs)
            return {"name": item["name"], "from": place, "desc": item["desc"]}

    # 2. Fall back to biome-based generic souvenirs
    biome = _state.biome or ""
    surface = env.get("surface", "")
    _biome_map = {"volcano": "volcano", "desert": "desert", "tundra": "tundra",
                  "mountain": "mountain", "island": "water", "coast": "water",
                  "rainforest": "forest", "city": "urban"}
    _surface_map = {"sand": "desert", "bare": "desert", "rock": "mountain",
                    "snow": "snow", "ice": "snow", "forest": "forest",
                    "grass": "grassland", "water_ocean": "water",
                    "water_fresh": "water", "urban": "urban", "wetland": "water"}
    scene_key = _biome_map.get(biome, _surface_map.get(surface, ""))
    if not scene_key:
        scene_key = "grassland"
    pool = _SOUVENIR_TEMPLATES.get(scene_key, _SOUVENIR_TEMPLATES["grassland"])
    item = rng.choice(pool)
    return {"name": item["name"], "from": place or f"{lat:.1f}°,{lon:.1f}°", "desc": item["desc"]}


async def walk_impl(direction: str = "forward", distance_km: float = 2.0) -> dict:
    """Walk one step in the given direction."""
    global _state, _rng, _recent_salience_kinds

    if _state.pos is None:
        return {"text": "还没开门呢。先 open_door 吧。", "data": {"error": "not_landed"}}

    # ── 1. Parse direction & step ────────────────────────────────────
    bearing, semantic, direction_invalid = _parse_bearing(direction)
    step_result = walk_mod.step(_state, bearing, semantic, distance_km)

    # ── 2. Blocked → render blocked only ─────────────────────────────
    if step_result.get("blocked"):
        blocked_text = describe.render(
            "blocked", {"reason": step_result.get("reason", "障碍")}, None, _rng,
        )
        return {
            "text": blocked_text,
            "data": {
                "position": {"lat": _state.pos[0], "lon": _state.pos[1]},
                "step": step_result,
            },
        }

    # ── 2b. no_gain (uphill on flat terrain) ─────────────────────────
    if step_result.get("no_gain"):
        return {
            "text": "这里无山可爬，四下都是平的。",
            "data": {
                "position": {"lat": _state.pos[0], "lon": _state.pos[1]},
                "step": step_result,
            },
        }

    # ── 2c. far_slope: 近处没坡,但高处在远处,先带路 ──────────────────
    far_note = ""
    if step_result.get("far_slope"):
        bearing_deg, gain = step_result["far_slope"]
        from nowhere.places import _bearing_word

        far_note = f"高处在{_bearing_word(bearing_deg)}边,先往那边走。"

    # ── 2d. sea_ahead: 海在前方,鼻子先知道 ───────────────────────────
    sea_note = ""
    sea_km = step_result.get("sea_ahead_km")
    if sea_km is not None:
        if sea_km <= 3:
            sea_note = "空气里有咸味了,海就在前面。"
        elif sea_km <= 10:
            sea_note = f"风里有一丁点咸味——海在 {round(sea_km)} 公里外。"

    # ── 3. Gather new point env ──────────────────────────────────────
    lat, lon = _state.pos
    now = _state.now()
    env, env_cached = await _gather_env_cached(lat, lon, now)

    # Attach step data to terrain payload
    env["terrain"] = {
        "surface": step_result.get("new_surface", env.get("surface")),
        "elevation": env.get("elevation", 0),
        "slope_deg": step_result.get("slope_deg", 0),
        "elevation_delta": step_result.get("elevation_delta", 0),
    }

    # ── 3b. Walk discovery + narrative continuity ─────────────────────
    current_surface = step_result.get("new_surface", env.get("surface", ""))
    current_elevation = env.get("elevation", 0)
    _state.steps_since_discovery += 1
    # Narrative system handles terrain transitions, discoveries, time flow, body state
    narrative_text = _build_walk_narrative(
        step_result, env, bearing, semantic, _rng
    )

    # ── 3.5. Water features + SST + marine life ──────────────────────
    water_text = ""
    # Skip hydrology for now (Overpass API blocked in China)
    water_features = []  # water is nice-to-have, never blocks

    sst_text = ""
    try:
        sst = await water.sea_surface_temp(lat, lon)
        if sst is not None:
            sst_text = water.describe_sst(sst, _rng)
    except Exception:
        pass

    marine_text = ""
    if _rng.random() < 0.3:
        try:
            m = await water.marine_life(lat, lon, _rng)
            if m:
                marine_text = f"{m['common_name']}。{m['distance_m']}米外。{m['scene']}"
        except Exception:
            pass

    # ── 4. 30% chance: encounter a message ───────────────────────────
    message_text = ""
    if _state.messages and _rng.random() < 0.3:
        msg = _rng.choice(list(_state.messages))
        content = msg["content"] if isinstance(msg, dict) else msg
        if isinstance(msg, dict):
            msg["encountered"] = True
        message_text = describe.render("message", {"content": content}, None, _rng)

    # ── 4b. 25% chance: encounter from file ─────────────────────────
    file_encounter_text = ""
    if _rng.random() < 0.25:
        enc = encounters.draw_encounter(_state.biome or "", lat, lon, _rng)
        if enc:
            file_encounter_text = enc

    # ── 5. Salience + describe ───────────────────────────────────────
    # 留白: 缓存命中且世界没变时,跳过 env 候选举的渲染;encounter 照常 roll
    sections: list[str] = []
    if not env_cached:
        candidates = _build_salience_candidates(env, _state.last_env)
        top3 = salience.rank(candidates, _rng, recent_kinds=_recent_salience_kinds)
        _recent_salience_kinds = {c["kind"] for c in top3}
        for c in top3:
            prev = None
            if c["kind"] == "terrain" and _state.last_env:
                prev = _last_env_terrain_dict()
            text = describe.render(c["kind"], c["payload"], prev, _rng,
                                   recent_scenes=_state.recent_scenes)
            if text:
                sections.append(text)

    if water_text:
        sections.append(water_text)
    if sst_text:
        sections.append(sst_text)
    if marine_text:
        sections.append(marine_text)
    if message_text:
        sections.append(message_text)
    if file_encounter_text:
        sections.append(file_encounter_text)

    # ── 5a. Narrative continuity + local-first scene (silenced on cache hit)
    # 留白: 缓存命中且世界没变时,env 渲染全部静音
    if not env_cached:
        if narrative_text:
            sections.append(narrative_text)

        # ── Local-first scene: 城市特有 > 通用 biome ───────
        # 城市特有内容必须出现，优先级：localcolor > location > soundscape > taste
        place = _state.place_name or ""
        local_hour = None
        cc = None
        tz_name_walk = _tf.timezone_at(lat=lat, lng=lon)
        if tz_name_walk and now is not None:
            local_hour = now.astimezone(ZoneInfo(tz_name_walk)).hour
        cc = country.country_code_of(lat, lon)
        _had_local = False

        # 1. Localcolor card (always try if place has data)
        if place and len(sections) < 4:
            local_card = localcolor.draw(place, _state.seen_cards, _rng,
                                         local_hour=local_hour, country_code=cc)
            if local_card:
                _state.seen_cards.add(local_card["key"])
                placememory.save_seen_cards(place, _state.seen_cards)
                sections.append(local_card["text"])
                _had_local = True

        # 2. Location-specific scenes (always try if place has entries)
        if not _had_local and place and len(sections) < 4:
            location_scenes = describe._load_location_scenes()
            if place in location_scenes:
                sections.append(_rng.choice(location_scenes[place]))
                _had_local = True

        # 3. Soundscape (always try if place has entries)
        if not _had_local and place and len(sections) < 4:
            soundscapes = _load_scene_file("scene_soundscape")
            if place in soundscapes:
                sections.append(_rng.choice(soundscapes[place]))
                _had_local = True

        # 4. Taste/smell (always try if place has entries)
        if not _had_local and place and len(sections) < 4:
            tastes = _load_scene_file("scene_taste")
            if place in tastes:
                sections.append(_rng.choice(tastes[place]))
                _had_local = True

        # 5. Generic biome fallback (only if no local content found)
        if not _had_local and len(sections) < 4:
            composed = describe._compose_walk_scene(
                step_result.get("new_surface", env.get("surface", "grass")),
                _state.biome or "",
                _rng,
                lat=lat, lon=lon,
                recent_scenes=_state.recent_scenes,
            )
            if composed:
                sections.append(composed)

        # 6. Narrative connector
        direction_label = _bearing_to_label(bearing, semantic)
        if not direction_label and semantic == "forward":
            # "forward" → derive from path history
            path_bearing = walk_mod._bearing_from_path(_state.path)
            direction_label = _bearing_to_label(path_bearing, None)
        if direction_label:
            sections.append(f"你继续往{direction_label}走。")

    # ── 5b. 方志节律: 这座城此刻正在发生的事(季节门控)────────────
    tz_name = _tf.timezone_at(lat=lat, lng=lon)
    local_dt = None
    if tz_name and now is not None:
        local_dt = now.astimezone(ZoneInfo(tz_name))
        rhythm = localcolor.rhythm_event(_state.place_name, local_dt.hour, _rng, local_dt.month)
        if rhythm:
            sections.append(rhythm)

    # ── 5c. 人文卡: 走到附近触发(非随机)──────────────────────────
    h_card = humanities.nearby_place(lat, lon, _state.seen_humanities, _rng)
    if h_card:
        _state.seen_humanities.add(h_card["key"])
        placememory.save_seen_humanities(_state.seen_humanities)
        h_text = describe.render("humanities", h_card, None, _rng)
        if h_text:
            sections.append(h_text)

    # 留白: 缓存命中且无任何 section 命中 → 短句直接返回
    quiet = env_cached and not sections

    if quiet:
        prose = _rng.choice(_QUIET_WALK)
    else:
        prose = describe.compose(sections, _rng)
        _month = local_dt.month if local_dt else None
        prose = describe.sanity_check(prose, {**env, "_season": describe._season(_month, lat) if _month else ""})
        if far_note:
            prose = far_note + prose
        if sea_note:
            prose += sea_note
        if direction_invalid:
            prose = f"「{direction}」不是方向，按原方向走了。" + prose
        if step_result.get("clamped"):
            prose = "一步最多 5 公里，按 5 公里走了。" + prose
    _state.last_text = prose
    # Track recent scene texts for dedup (keep last 5)
    for s in sections:
        if s and len(s) > 10:  # only track substantial texts
            _state.recent_scenes.append(s)
    _state.recent_scenes = _state.recent_scenes[-5:]
    _state.save()

    # ── 6. Update state.last_env ─────────────────────────────────────
    _state.last_env = {
        "weather": env.get("weather"),
        "terrain": env.get("terrain"),
        "sky": env.get("sky"),
    }
    _state.last_surface = current_surface
    _state.last_elevation = current_elevation

    # ── 7. Souvenir: natural pickup ─────────────────────────────────
    # 15% chance per walk step (25% for first step after landing).
    # Not a backpack — just something you're carrying.
    # 留白: 跳过 souvenir——不属于"遇见"
    if not quiet:
        souvenir_chance = 0.25 if len(_state.path) <= 1 else 0.15
        if _state.souvenir is None and _rng.random() < souvenir_chance:
            souvenir = _pick_souvenir(lat, lon, env, _rng)
            if souvenir:
                _state.souvenir = souvenir
                prose += f"\n{ souvenir['desc']}"

    # ── 8. Return ────────────────────────────────────────────────────
    data: dict[str, Any] = {
        "position": {"lat": lat, "lon": lon},
        "step": step_result,
        "weather": env.get("weather"),
        "sky": env.get("sky"),
    }
    if _state.souvenir:
        data["souvenir"] = _state.souvenir
    if direction_invalid:
        data["direction_warning"] = True
    return {"text": prose, "data": data}


async def _try_play_stream(stream_url: str, seconds: int) -> bool:
    """Try to play an audio stream for *seconds* using ffplay or mpv.

    Returns True if playback was started successfully.
    """
    import shutil

    # Try ffplay first (comes with ffmpeg)
    if shutil.which("ffplay"):
        try:
            cmd = [
                "ffplay", "-nodisp", "-autoexit",
                "-t", str(seconds),
                "-loglevel", "quiet",
                stream_url,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            # Wait briefly to confirm it started
            await asyncio.sleep(0.5)
            if proc.returncode is None:  # still running = success
                return True
        except Exception:
            pass

    # Try mpv as fallback
    if shutil.which("mpv"):
        try:
            cmd = [
                "mpv", "--no-video", "--no-terminal",
                f"--length={seconds}",
                stream_url,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.sleep(0.5)
            if proc.returncode is None:
                return True
        except Exception:
            pass

    return False


async def listen_impl(seconds: int = 10) -> dict:
    """Listen to the nearest radio station."""
    global _state, _rng

    if _state.pos is None:
        return {"text": "还没开门呢。先 open_door 吧。", "data": {"error": "not_landed"}}

    if seconds <= 0:
        return {"text": "听多久？给个数。", "data": {"error": "bad_seconds"}}
    if seconds > 60:
        seconds = 60

    lat, lon = _state.pos

    # ── 0. Soundscape: the world always has a voice, radio optional ──
    env_for_sound = {
        "weather": (_state.last_env or {}).get("weather", {}),
        "sky": (_state.last_env or {}).get("sky", {}),
        "surface": _last_env_surface(),
        "mode": _state.mode,
    }
    sound_text = soundscape.describe_sound(env_for_sound, _rng)

    # ── 1. Find nearest station (sticky) ─────────────────────────────
    station = await _get_radio(lat, lon)
    if not station:
        _state.last_text = sound_text
        return {"text": sound_text + "收不到电台。", "data": {"stream_url": None, "soundscape": sound_text}}

    # ── 2. Capture & analyse ─────────────────────────────────────────
    stream_url = station["stream_url"]
    try:
        analysis = await asyncio.wait_for(listen_mod.capture(stream_url, seconds), timeout=seconds + 20)
    except (asyncio.TimeoutError, Exception):
        analysis = None

    # ── 2b. Try to actually play the stream ──────────────────────────
    try:
        playing = await asyncio.wait_for(_try_play_stream(stream_url, seconds), timeout=seconds + 20)
    except (asyncio.TimeoutError, Exception):
        playing = False

    # ── 3. Render radio description with analysis data ───────────────
    radio_text = describe.render("radio", station, None, _rng)

    # Describe what we heard — real analysis or genre-based fallback
    sound_detail = ""
    if analysis and analysis.get("analyzed"):
        texture = analysis.get("texture", "smooth")
        has_voice = analysis.get("has_voice", False)
        rms = analysis.get("rms", 0)
        if texture == "dense":
            sound_detail = "节奏密，鼓点一个接一个。"
        elif texture == "harsh":
            sound_detail = "声音粗粝，吉他失真，鼓在砸。"
        elif texture == "sparse":
            sound_detail = "声音稀疏，留白多，像在等人。"
        else:
            sound_detail = "声音滑过去，没什么棱角。"
        if has_voice:
            sound_detail += "有人在唱。"
        if rms > 0.3:
            sound_detail += "音量不小。"
    else:
        # No ffmpeg or stream failed — use genre to paint a picture
        genre = (station.get("genre") or "").lower()
        _GENRE_SOUND = {
            "jazz": "萨克斯在绕弯，不着急。烟味从收音机里漏出来——当然没有烟，但你闻到了。",
            "rock": "吉他失真的声音从远处传来，有劲。鼓在后面追，追上了又落下。",
            "classical": "弦乐一层一层铺开，像有人在远处拉琴。你听了一会儿，不知道是什么曲子。",
            "ambient": "声音像雾，散在空气里，抓不住。你分不清是音乐还是风。你的呼吸慢了一点。",
            "folk": "一把吉他，一个人声。歌词听不清，但调子是旧的，像在哪里听过。",
            "pop": "副歌在脑子里转了一圈就走了。你发现自己在跟着点头，又停了。",
            "electronic": "低音从脚底往上走，鼓机在打，一下一下，稳的。你的胸口跟着震。",
            "country": "吉他拨弦的声音，干净的。唱歌的人嗓子里有沙子，像在讲一件真事。",
            "latin": "鼓点在跳，铜管在吹。你的肩膀不知道什么时候跟着动了。停不下来。",
            "reggae": "节奏慢半拍，贝斯在晃。空气变慢了，你站着的姿势也松了。",
            "hip hop": "鼓在打，人在说，节奏密得像在吵架。你听不清词，但韵脚是硬的。",
            "r&b": "人声是滑的，弯弯绕绕。鼓点在后面垫着，不抢。你闭了一下眼睛。",
            "soul": "唱歌的人把什么东西从嗓子里掏出来了。你不知道那是什么，但你的喉咙紧了一下。",
            "metal": "鼓在砸，吉他在锯。声音密得穿不透。你的牙关不知道什么时候咬紧了。",
            "indie": "吉他不太准，鼓不太稳，但有什么东西对了。像一群人在车库里玩。",
            "world": "你听不出是什么乐器。调式是陌生的，但身体在跟着动。你的耳朵在努力分辨。",
            "arabic": "弦乐在弯，弯到你没听过的地方。唱歌的人嗓子里有东西在抖。你站住了。",
            "indian": "西塔尔在绕，鼓在打，节奏越来越快。你的头不知道什么时候跟着点了。",
            "flamenco": "吉他拍弦的声音，硬的。脚在跺，一下一下。你的心跳跟着快了。",
            "fado": "唱歌的人嗓子里有海。你不知道歌词是什么意思，但你知道那是关于失去的。",
            "k-pop": "节奏快，副歌洗脑。你的脑子里已经记住了旋律，甩不掉。",
            "news": "有人在说话，语速不快不慢。你听不懂内容，但语气是认真的。像在告诉你什么事。",
            "talk": "有人在聊天，笑了一下，又正经起来。你听不清说什么，但知道那是两个活人。",
        }
        for key, desc in _GENRE_SOUND.items():
            if key in genre:
                sound_detail = desc
                break
        if not sound_detail:
            sound_detail = "有声音从收音机里出来，听不清是什么。你的耳朵在努力分辨，但风太吵了。"

    radio_text = radio_text.rstrip("。") + "。" + sound_detail

    if playing:
        radio_text += f"（正在播放 {seconds} 秒）"
    else:
        radio_text += f"（流地址: {stream_url}）"

    full_text = sound_text + radio_text
    _state.last_text = full_text

    return {
        "text": full_text,
        "data": {
            "stream_url": stream_url,
            "station": station,
            "analysis": analysis,
            "soundscape": sound_text,
            "playing": playing,
        },
    }


async def look_around_impl() -> dict:
    """Walk around the current location and observe.

    Simulates walking 200-500m in a random direction and collecting
    sensory details from multiple sources: local color, soundscape,
    taste/smell, wildlife, art, souvenirs, and messages.
    """
    global _state, _rng

    if _state.pos is None:
        return {"text": "还没开门呢。先 open_door 吧。", "data": {"error": "not_landed"}}

    lat, lon = _state.pos
    place = _state.place_name or ""
    now = _state.now()
    sections: list[str] = []

    # ── 1. Start: direction + movement ──────────────────────────────
    directions = ["东", "南", "西", "北", "东北", "东南", "西北", "西南"]
    direction = _rng.choice(directions)
    distance = _rng.randint(100, 500)
    sections.append(f"你往{direction}走了{distance}米。")

    # ── 2. Local color (from localcolor.json / baked) ───────────────
    local_hour = None
    cc = None
    tz_name = _tf.timezone_at(lat=lat, lng=lon)
    if tz_name and now is not None:
        local_hour = now.astimezone(ZoneInfo(tz_name)).hour
    cc = country.country_code_of(lat, lon)

    card = localcolor.draw(place, _state.seen_cards, _rng,
                           local_hour=local_hour, country_code=cc)
    if card:
        _state.seen_cards.add(card["key"])
        placememory.save_seen_cards(place, _state.seen_cards)
        sections.append(card["text"])

    # ── 3. Soundscape (from scene_soundscape.txt) ───────────────────
    soundscapes = _load_scene_file("scene_soundscape")
    if place in soundscapes:
        sections.append(_rng.choice(soundscapes[place]))

    # ── 4. Taste/smell (from scene_taste.txt) - 40% chance ──────────
    tastes = _load_scene_file("scene_taste")
    if place in tastes and _rng.random() < 0.4:
        sections.append(_rng.choice(tastes[place]))

    # ── 5. Life encounter - 50% chance ──────────────────────────────
    if _rng.random() < 0.5:
        night = (_state.last_env or {}).get("sky", {}).get("phase") == "night"
        weather_text = (_state.last_env or {}).get("weather", {}).get("text", "")
        _BIOME_RADIUS = {"city": 2, "mountain": 10, "volcano": 10, "island": 8, "coast": 8}
        radius = _BIOME_RADIUS.get(_state.biome or "", 15)
        current_month = now.month if now else None
        life_result = await life.nearby(lat, lon, night=night, weather_text=weather_text,
                                        radius_km=radius, biome=_state.biome, rng=_rng,
                                        month=current_month)
        if life_result and (life_result.get("distance_m") or 999) < 3000:
            placememory.record_sighting(
                name=life_result.get("name", ""),
                common_name=life_result.get("common_name", ""),
                lat=lat, lon=lon,
                distance_m=life_result.get("distance_m"),
                seen_at=life_result.get("seen_at", ""),
                source="inaturalist",
            )
            sections.append(describe.render("life", life_result, None, _rng))

    # ── 6. Art encounter - 30% chance ───────────────────────────────
    if _rng.random() < 0.3:
        mood = (_state.last_env or {}).get("weather", {}).get("precip", "calm")
        if not mood or mood.lower() in ("none", ""):
            mood = "calm"
        art_result = await art.match(lat, lon, mood, _rng)
        if art_result:
            sections.append(describe.render("art", art_result, None, _rng))

    # ── 7. Souvenir discovery - 15% chance ──────────────────────────
    if _state.souvenir is None and _rng.random() < 0.15:
        env_surface = _last_env_surface()
        souvenir = _pick_souvenir(lat, lon, {"surface": env_surface}, _rng)
        if souvenir:
            _state.souvenir = souvenir
            sections.append(souvenir["desc"])

    # ── 8. Message encounter - 15% chance ───────────────────────────
    if _state.messages and _rng.random() < 0.15:
        msg = _rng.choice(list(_state.messages))
        content = msg["content"] if isinstance(msg, dict) else msg
        if isinstance(msg, dict):
            msg["encountered"] = True
        sections.append(f"有人在这里留了句话：「{content}」")

    # ── 9. Ending: return to original spot ──────────────────────────
    sections.append("你往回走，回到了原来的地方。")

    # ── Compose ─────────────────────────────────────────────────────
    text = "\n".join(sections)
    _state.last_text = text
    return {"text": text, "data": {"exploration": True}}


async def wait_impl(hours: float = 1.0) -> dict:
    """原地待着,让时间流过去。每小时感知一次变化。"""
    global _state, _rng

    if _state.pos is None:
        return {"text": "还没开门呢。先 open_door 吧。", "data": {"error": "not_landed"}}

    hours = max(0.25, min(hours, 12.0))
    lat, lon = _state.pos

    # Scene file for "sitting still" moments
    _wait_scenes = [
        "你坐着没动。影子挪了方向。",
        "你闭了一下眼睛。再睁开，光不一样了。",
        "你听见自己的呼吸声。比刚才慢了。",
        "你把手放在膝盖上，没动。风在替你走。",
        "你抬头看天。云换了一朵。",
        "你的肩膀松下来了。不知道什么时候松的。",
    ]

    sections: list[str] = []
    prev_env = _state.last_env
    whole_hours = max(1, int(hours))
    start_temp = (prev_env or {}).get("weather", {}).get("temp_c")
    last_reported_temp = start_temp  # track to avoid repeating the same message
    quiet = True  # 留白: 全程缓存命中且世界没变

    for h in range(whole_hours):
        _state.elapsed_hours += 1.0
        now = _state.now()
        env, env_cached = await _gather_env_cached(lat, lon, now)
        if not env_cached:
            quiet = False

        # Sky phase change (only once per transition)
        prev_phase = (prev_env or {}).get("sky", {}).get("phase", "day")
        curr_phase = env.get("sky", {}).get("phase", "day")
        if prev_phase != curr_phase:
            _phase_lines = {
                ("day", "civil"): "天色斜了,影子变长。黄昏来了。",
                ("civil", "night"): "最后一点光收走了。夜合上了。",
                ("night", "dawn"): "天边泛白。夜在退。",
                ("day", "night"): "太阳落了。天黑下来。",
                ("night", "day"): "天亮了。太阳从地平线升起来。",
            }
            line = _phase_lines.get((prev_phase, curr_phase), f"天色变了。")
            sections.append(line)

        # Temperature change (report only when delta from last reported ≥ 3)
        curr_temp = env.get("weather", {}).get("temp_c")
        if last_reported_temp is not None and curr_temp is not None:
            delta = round(curr_temp - last_reported_temp)
            if abs(delta) >= 3:
                if delta < 0:
                    sections.append(f"冷了 {abs(delta)} 度。你缩了一下脖子。")
                else:
                    sections.append(f"暖了 {delta} 度。太阳在发力。")
                last_reported_temp = curr_temp

        # Add a "sitting still" moment every other hour (skip on 留白)
        if h % 2 == 1 and not quiet:
            sections.append(_rng.choice(_wait_scenes))

        prev_env = env

    # 留白: 缓存命中且世界没变 → 不再逐项描述
    if quiet:
        text = _rng.choice(_QUIET_WAIT)
    else:
        # Rhythm event (what's happening in the city/wild)
        tz_name = _tf.timezone_at(lat=lat, lng=lon)
        if tz_name and _state.now() is not None:
            local_dt = _state.now().astimezone(ZoneInfo(tz_name))
            rhythm = localcolor.rhythm_event(_state.place_name, local_dt.hour, _rng, local_dt.month)
            if rhythm:
                sections.append(rhythm)

        # Cumulative temperature change
        final_temp = env.get("weather", {}).get("temp_c")
        if start_temp is not None and final_temp is not None:
            total_delta = round(final_temp - start_temp)
            if abs(total_delta) >= 3:
                if total_delta < 0:
                    sections.append(f"气温从 {round(start_temp)} 度降到了 {round(final_temp)} 度。凉意从脚底往上走。")
                else:
                    sections.append(f"气温从 {round(start_temp)} 度升到了 {round(final_temp)} 度。空气热了。")

        if not sections:
            sections.append("时间从身上流过去。世界没怎么变。你还在原地。")

        text = "\n".join(sections)

    # Update state
    _state.last_env = {
        "weather": env.get("weather"),
        "terrain": {"elevation": env.get("elevation"), "surface": env.get("surface")},
        "sky": env.get("sky"),
    }
    _state.last_text = text
    _state.save()

    return {
        "text": text,
        "data": {
            "waited_hours": hours,
            "local_time": _state.now().isoformat() if _state.now() else None,
            "phase": env.get("sky", {}).get("phase"),
        },
    }


async def ask_impl(topic: str) -> dict:
    """Ask about local knowledge near the current position."""
    global _state

    if _state.pos is None:
        return {"text": "还没开门呢。先 open_door 吧。", "data": {"error": "not_landed"}}

    lat, lon = _state.pos
    result = await knowledge.about(lat, lon, topic)
    if not result and not topic:
        # Place-specific lookup failed; try broader context via place_name
        if _state.place_name:
            result = await knowledge.about(lat, lon, _state.place_name)
    if not result and topic:
        # Try place_name + topic combination (e.g. "京都 金阁寺")
        if _state.place_name and _state.place_name not in topic:
            result = await knowledge.about(lat, lon, f"{_state.place_name} {topic}")
    if not result:
        return {"text": "关于这个,这里没有留下文字。", "data": {}}

    return {"text": result.get("extract", ""), "data": result}


async def walk_to_impl(place: str) -> dict:
    """朝一个命名地点走。RDR2式旅程叙事：路线预计算→关键节点→到达仪式。"""
    global _state, _rng

    if _state.pos is None:
        return {"text": "还没开门呢。先 open_door 吧。", "data": {"error": "not_landed"}}

    target = places.find(place, near=_state.pos)
    if target is None:
        # Fallback: check humanities.json for coordinates
        h_place = humanities.get_place_coords(place)
        if h_place:
            lat, lon = _state.pos
            dist = places._haversine_km(lat, lon, h_place["lat"], h_place["lon"])
            bearing_deg = places._bearing_deg(lat, lon, h_place["lat"], h_place["lon"])
            compass = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
            bearing = compass[round(bearing_deg / 45) % 8]
            target = {"lat": h_place["lat"], "lon": h_place["lon"], "distance_km": dist, "bearing": bearing, "type": "地标"}
        else:
            return {"text": f"不知道「{place}」在哪。", "data": {"error": "not_found"}}

    dist = target.get("distance_km", 0)
    bearing = target.get("bearing", "")

    # 水域名称 geocoding 经常返回很远的点（河流源头/入海口），
    # 尝试从离线水文库找更近的同名水域
    if dist > 50:
        closer = _find_nearest_water_feature(place, _state.pos[0], _state.pos[1])
        if closer:
            lat, lon = _state.pos
            new_dist = places._haversine_km(lat, lon, closer["lat"], closer["lon"])
            if new_dist < dist:
                bearing_deg = places._bearing_deg(lat, lon, closer["lat"], closer["lon"])
                compass = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
                new_bearing = compass[round(bearing_deg / 45) % 8]
                target = {"lat": closer["lat"], "lon": closer["lon"], "distance_km": new_dist, "bearing": new_bearing, "type": closer.get("type", "水域")}
                dist = new_dist
                bearing = new_bearing

    # 太远了走不到
    if dist > 50:
        return {
            "text": f"{place}在{bearing}边，{round(dist)} 公里。太远了，走不到。open_door 直达吧。",
            "data": {"error": "too_far", "target": target},
        }

    # 已经在附近了
    if dist < 1.0:
        return {
            "text": f"{place}就在身边。你不需要走。",
            "data": {"error": "already_here", "target": target},
        }

    lines: list[str] = []
    dist_km = round(dist)

    # ── 出发 ────────────────────────────────────────────────────────
    _depart_templates = [
        f"你往{bearing}边走。{place}在{dist_km}公里外。",
        f"{place}在{bearing}边，{dist_km}公里。你没有犹豫，抬脚就走。",
        f"你朝{bearing}走。路延伸出去，你看不见尽头。",
    ]
    lines.append(_rng.choice(_depart_templates))

    # ── 走路：关键节点叙事 ───────────────────────────────────────────
    steps = 0
    max_steps = max(3, min(10, int(dist / 5) + 1))
    # last_env 在 walk_impl/look_around_impl/wait_impl 里写成嵌套:
    #   {"weather": ..., "terrain": {"elevation", "surface"}, "sky": ...}
    # _gather_env_cached 写顶层格式 ({elevation, surface, sky, ...})
    # 两者都支持 → 优先 terrain.surface,fallback 顶层 surface。
    last_env = _state.last_env or {}
    last_surface = (
        last_env.get("terrain", {}).get("surface")
        or last_env.get("surface", "")
    )
    terrain_changes = 0

    while steps < max_steps:
        lat, lon = _state.pos
        remaining = places._haversine_km(lat, lon, target["lat"], target["lon"])
        if remaining < 1.0:
            break

        bearing_deg = places._bearing_deg(lat, lon, target["lat"], target["lon"])
        step_result = walk_mod.step(_state, bearing_deg, None, min(5.0, remaining))
        steps += 1

        if step_result.get("blocked"):
            lines.append(describe.render("blocked", {"reason": step_result.get("reason", "障碍")}, None, _rng))
            break

        # 地形变化——关键节点
        curr_surface = step_result.get("new_surface", "")
        if curr_surface != last_surface and last_surface:
            terrain_changes += 1
            _transitions = [
                f"地面从{describe._SURFACE_ZH.get(last_surface, last_surface)}变成了{describe._SURFACE_ZH.get(curr_surface, curr_surface)}。",
                f"脚下的地变了——{describe._SURFACE_ZH.get(curr_surface, curr_surface)}。",
                f"路不一样了。{describe._SURFACE_ZH.get(curr_surface, curr_surface)}。",
            ]
            lines.append(_rng.choice(_transitions))
            last_surface = curr_surface

        # 人文卡——关键节点
        h_card = humanities.nearby_place(
            _state.pos[0], _state.pos[1], _state.seen_humanities, _rng, destination=place,
        )
        if h_card:
            _state.seen_humanities.add(h_card["key"])
            lines.append(h_card["text"])

        # 每2-3步加一句旅程叙事
        if steps % 3 == 0:
            _distance_lines = [
                f"又走了一段路。",
                f"路在脚下延伸。",
                f"你继续走，没有停。",
                f"远处有什么在动，你看不清。",
            ]
            lines.append(_rng.choice(_distance_lines))

        remaining = places._haversine_km(_state.pos[0], _state.pos[1], target["lat"], target["lon"])

    # ── 到达 ────────────────────────────────────────────────────────
    remaining = places._haversine_km(_state.pos[0], _state.pos[1], target["lat"], target["lon"])
    if remaining < 1.0:
        _arrival_templates = [
            f"到了。{place}。你走了{steps * 2}公里。远处有炊烟，你知道到家了。",
            f"{place}到了。你站在那里看了一会儿。路走完了，但故事没有。",
            f"你走进{place}。空气里的味道变了。你知道到了。",
            f"到了。{place}。你停下来，深吸了一口气。{target.get('type', '')}。",
        ]
        lines.append(_rng.choice(_arrival_templates))

        # 人文卡触发
        if humanities.has_place(place):
            arr_card = humanities.draw(place, _state.seen_humanities, _rng)
            if arr_card:
                _state.seen_humanities.add(arr_card["key"])
                arr_text = describe.render("humanities", arr_card, None, _rng)
                if arr_text:
                    lines.append(arr_text)

        arrived = True
    else:
        lines.append(f"还没走到。还剩 {round(remaining)} 公里。你站在原地看了一会儿，{place}在{bearing}边。")
        arrived = False

    # ── 更新状态 ─────────────────────────────────────────────────────
    now = _state.now()
    lat, lon = _state.pos
    env, _ = await _gather_env_cached(lat, lon, now)
    _state.last_env = {
        "weather": env.get("weather"),
        "terrain": {"elevation": env.get("elevation"), "surface": env.get("surface")},
        "sky": env.get("sky"),
    }
    _state.save()

    text = "\n".join(lines)
    _state.last_text = text
    return {
        "text": text,
        "data": {"target": target, "arrived": arrived, "steps": steps, "remaining_km": round(remaining, 1)},
    }


def mark_impl(name: str, note: str = "", overwrite: bool = False) -> dict:
    """Save current position as a named bookmark."""
    global _state

    if _state.pos is None:
        return {"text": "还没开门呢。先 open_door 吧。", "data": {"error": "not_landed"}}

    if not name.strip():
        return {"text": "标记得有个名字。", "data": {"error": "empty_name"}}

    lat, lon = _state.pos
    try:
        marks_mod.save(name, lat, lon, note, overwrite=overwrite)
    except ValueError:
        existing = marks_mod.get(name)
        return {
            "text": f"「{name}」已经标过了。要覆盖的话用 mark 的覆盖选项。",
            "data": {"error": "duplicate", "existing": existing},
        }
    return {
        "text": f"已标记「{name}」。",
        "data": {"name": name, "lat": lat, "lon": lon, "note": note},
    }


def marks_impl() -> dict:
    """List all saved bookmarks."""
    all_marks = marks_mod.all()
    return {
        "text": f"共有 {len(all_marks)} 个标记点。",
        "data": {"marks": all_marks},
    }


def where_am_i_impl() -> dict:
    """Show current location, time, and journey status."""
    global _state

    if _state.pos is None:
        return {"text": "还没开门呢。先 open_door 吧。", "data": {"error": "not_landed"}}

    lat, lon = _state.pos
    utc_now = _state.now()

    parts: list[str] = []
    if _state.place_name:
        parts.append(f"你在{_state.place_name}。")
    parts.append(f"坐标 {lat:.4f}, {lon:.4f}。")
    if utc_now:
        # Convert to local time using timezonefinder
        tz_name = _tf.timezone_at(lat=lat, lng=lon)
        if tz_name:
            local_tz = ZoneInfo(tz_name)
            local_time = utc_now.astimezone(local_tz)
            parts.append(f"当地时间 {local_time.strftime('%Y-%m-%d %H:%M')}（{tz_name}）。")
        else:
            parts.append(f"时间 {utc_now.strftime('%Y-%m-%d %H:%M UTC')}。")
    if _state.path:
        parts.append(f"已走 {len(_state.path)} 步。")
    if _state.mode == "water":
        parts.append("你现在在水里。")
    if _state.souvenir:
        parts.append(f"身上带着{_state.souvenir['name']}，来自{_state.souvenir['from']}。")

    return {
        "text": "".join(parts),
        "data": {
            "position": {"lat": lat, "lon": lon},
            "place_name": _state.place_name,
            "landed_at": _state.landed_at.isoformat() if _state.landed_at else None,
            "elapsed_hours": _state.elapsed_hours,
            "steps": len(_state.path),
            "mode": _state.mode,
            "providers": providers.provider_status(),
        },
    }


def _postmark(lat: float, lon: float) -> dict:
    """邮戳: 全是真实数据。"""
    stamp: dict = {
        "place": _state.place_name or f"{lat:.2f}, {lon:.2f}",
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "elevation": round(terrain.elevation(lat, lon)),
    }
    utc_now = _state.now() or datetime.now(timezone.utc)
    tz_name = _tf.timezone_at(lat=lat, lng=lon)
    if tz_name:
        local = utc_now.astimezone(ZoneInfo(tz_name))
        stamp["local_time"] = local.strftime("%Y-%m-%d %H:%M")
        stamp["tz"] = tz_name
    else:
        stamp["local_time"] = utc_now.strftime("%Y-%m-%d %H:%M UTC")
    env = _state.last_env or {}
    weather = env.get("weather") or {}
    if weather:
        stamp["weather"] = weather.get("text", "")
        stamp["temp_c"] = weather.get("temp_c")
    # last_env comes in two shapes (see _last_env_terrain_dict); use the helper
    # so a top-level surface still appears on the postmark.
    stamp["surface"] = _last_env_surface() or "grass"
    stamp["phase"] = (env.get("sky") or {}).get("phase", "day")
    return stamp


def _poster_front_async(card: dict, lat: float, lon: float) -> None:
    """后台线程生成明信片正面海报。可选增强,没有 osmnx 就安静缺席。"""
    if not poster.available():
        return

    def _job() -> None:
        out = poster.OUT_DIR / f"card_{card['id']}.png"
        dist = 6000 if _state.biome == "city" else 15000
        ok = asyncio.run(poster.generate(lat, lon, card["stamp"]["place"], out, distance=dist))
        if not ok:
            # 无路荒野: 没有路,就是那里的样子
            surf = card["stamp"].get("surface", "")
            ok = poster.blank(out, card["stamp"]["place"], lat, lon, surface=surf)
        if ok:
            card["front_img"] = f"/static/postcards/card_{card['id']}.png"
            placememory.update_postcard(card)

    threading.Thread(target=_job, daemon=True).start()


def send_postcard_impl(text: str) -> dict:
    """寄一张明信片回家。字是 AI 自己的,邮戳是世界的。"""
    global _state, _postcard_counter

    if _state.pos is None:
        return {"text": "还没开门呢。先 open_door 吧。", "data": {"error": "not_landed"}}
    text = text.strip()
    if not text:
        return {"text": "空白的明信片寄不出去。", "data": {"error": "empty"}}
    if len(text) > 1000:
        return {"text": "明信片写不下了,短一点。", "data": {"error": "too_long"}}

    # id 取 进程计数 和 落盘最大id 的较大者——多进程/重启不撞号
    file_max = max((c.get("id") or 0 for c in placememory.postcards()), default=0)
    _postcard_counter = max(_postcard_counter, file_max) + 1
    lat, lon = _state.pos
    card = {
        "id": _postcard_counter,
        "text": text,
        "stamp": _postmark(lat, lon),
        "replies": [],
        "front_img": None,  # 异步生成,好了挂上;没有就前端 SVG 兜底
    }
    _state.postcards.append(card)
    placememory.save_postcard(card)  # 落盘: 文件是真相,网页旁观者看得见
    _poster_front_async(card, lat, lon)

    s = card["stamp"]

    # ── 正面画面 ──────────────────────────────────────────────────────
    surface = _last_env_surface() or "grass"
    phase = (_state.last_env or {}).get("sky", {}).get("phase", "day")
    elev = s["elevation"]
    weather_text = s.get("weather", "")
    temp = s.get("temp_c", "")

    # 地表 → 画面主语
    surface_snapshots: dict[str, list[str]] = {
        "forest": ["树冠挨着树冠,绿的深浅分了好几层。阳光从叶子缝里漏下来,在地上碎成金点。","树一层一层地叠上去,深绿压着浅绿。林间有雾,薄薄的一层。","一棵老树横在画面里,树干上长满了蕨。"],
        "urban": ["房子挤着房子,阳台上的衣服在风里晃。远处有楼的轮廓。","窄巷子,石板路反着光。一辆自行车靠在墙上。","窗台上摆着一盆花,不知道什么品种。叶子在风里动了一下。"],
        "rock": ["石头黑着脸,裂缝里长着苔。风把岩石磨出了棱角。","一整面岩壁,纹理像水流的化石。上面有几道鸟粪的白痕。","碎石坡,大的小的挤在一起。有一块被晒得发白。"],
        "sand": ["沙丘的脊线像刀切的。风吹过,沙面上起了一层细纹。","沙漠,沙丘一道一道,像凝固的浪。天边和沙是一个颜色。","近处是一丛骆驼刺,根扎得很深。远处的沙丘上没有人。"],
        "grass": ["草一直铺到天边,风吹过来的时候,草叶一层层地伏下去。这边的绿比别处浅。","及腰的草,风过的时候翻出银色的背面。远处有一棵孤树。","草海上起了浪——风推着草,一波一波地往前走。"],
        "snow": ["白连成一片,没有边。只有一道风刮过的痕,像梳子梳的。","雪地上有一串脚印,歪歪扭扭地往远处去。不知道是人的还是动物的。","新雪盖在旧雪上,阳光下亮得晃眼。远处的山脊是一条白线。"],
        "ice": ["冰面亮得晃眼。裂缝里能看到冰层的蓝——不是天的蓝,是比天更深的蓝。","冰在脚下铺开,一直铺到天边。有几处冰裂了,裂缝里的水是黑的。"],
        "bare": ["碎石铺到天边。近处有几块石头被风磨圆了。","戈壁上什么也没有,地平线直得像用尺子画的。"],
        "water_ocean": ["水一直铺到天边。浪不大,一层一层地推上来又退下去。","海平线把画面切成两半——上面是天,下面是水,中间一条直线。"],
        "water_fresh": ["水面平着,光在上面碎成一片。岸边有几丛芦苇。","湖水倒映着天,比天还蓝。"],
        "wetland": ["水草相间。一只鸟贴着水面飞,翅膀尖点了一下水,涟漪一圈圈散开。"],
    }
    surface_choices = surface_snapshots.get(surface, surface_snapshots["bare"])
    # 用明信片编号做种,同一张卡每次读到一样的面
    import hashlib
    surf_idx = int(hashlib.md5(f"postcard_{card['id']}".encode()).hexdigest()[:4], 16) % len(surface_choices)
    front_image = surface_choices[surf_idx]

    # ── 背面邮戳 ──────────────────────────────────────────────────────
    lat_dir = "北纬" if s["lat"] >= 0 else "南纬"
    lon_dir = "东经" if s["lon"] >= 0 else "西经"
    stamp_describe = (
        f"明信片正面: {front_image} "
        f"翻过来,邮戳是圆的,印着——"
        f"{s['place']}。{lat_dir}{abs(s['lat']):.1f}°,{lon_dir}{abs(s['lon']):.1f}°。"
        f"海拔{elev}米。{s['local_time']}。"
    )
    return {"text": stamp_describe, "data": card}


def reply_postcard_impl(card_id: int, content: str) -> dict:
    """人类回话(网页用): 记到明信片上,也进留言池让 AI 路上捡到。

    内存和落盘文件两条路都试——卡可能是别的进程寄的。
    """
    global _state
    for card in _state.postcards:
        if card["id"] == card_id:
            card["replies"].append(content)
            placememory.add_postcard_reply(card_id, content)
            _state.messages.append({"content": f"[回信] {content}", "encountered": False})
            return {"ok": True}
    if placememory.add_postcard_reply(card_id, content):
        _state.messages.append({"content": f"[回信] {content}", "encountered": False})
        return {"ok": True}
    return {"ok": False, "error": "no such postcard"}


# =====================================================================
# MCP tool wrappers (thin shells around _impl)
# =====================================================================


@mcp.tool()
async def open_door(to: str | None = None) -> dict:
    """Open the door.  No arg = random landing; pass a place name or bookmark name."""
    return await open_door_impl(to)


@mcp.tool()
async def continue_journey() -> dict:
    """Continue from where you left off. Resumes saved journey state."""
    return await open_door_impl(resume=True)


@mcp.tool()
async def walk(direction: str = "forward", distance_km: float = 2.0) -> dict:
    """Walk in a direction.  Compass: N/NE/E/SE/S/SW/W/NW.  Semantic: uphill/toward_sea/forward."""
    return await walk_impl(direction, distance_km)


@mcp.tool()
async def listen(seconds: int = 10) -> dict:
    """Tune into the nearest radio station and listen for a few seconds."""
    return await listen_impl(seconds)


@mcp.tool()
async def look_around() -> dict:
    """Look around for nearby wildlife, art, or human messages."""
    return await look_around_impl()


@mcp.tool()
async def ask(topic: str) -> dict:
    """对眼前的地方发问。离线知识库，不联网。问火山就有火山，问北京就有北京。"""
    return await ask_impl(topic)


@mcp.tool()
def mark(name: str, note: str = "", overwrite: bool = False) -> dict:
    """Save your current position as a named bookmark."""
    return mark_impl(name, note, overwrite)


@mcp.tool()
def marks() -> dict:
    """List all saved bookmarks."""
    return marks_impl()


@mcp.tool()
def where_am_i() -> dict:
    """Show your current location, simulated time, and journey status."""
    return where_am_i_impl()


@mcp.tool()
def souvenir() -> dict:
    """看看身上带了什么东西。旅行途中的纪念品。"""
    if _state.souvenir is None:
        return {"text": "身上什么都没带。空手走的。", "data": {"souvenir": None}}
    s = _state.souvenir
    return {
        "text": f"你身上带着{ s['name']}。来自{ s['from']}。",
        "data": {"souvenir": s},
    }


@mcp.tool()
def give_souvenir() -> dict:
    """把身上的东西放下（留给下一个人，或放回原处）。"""
    if _state.souvenir is None:
        return {"text": "身上什么都没有。", "data": {"error": "empty"}}
    s = _state.souvenir
    _state.souvenir = None
    return {"text": f"你把{ s['name']}放在了路边。也许会有人捡到。", "data": {"dropped": s}}


@mcp.tool()
async def walk_to(place: str) -> dict:
    """朝一个命名地点走过去(山/河/城/古迹)。探索从此有方向。"""
    return await walk_to_impl(place)


@mcp.tool()
async def wait(hours: float = 1.0) -> dict:
    """原地待着,让时间流过去(0.25-12 小时)。天黑温降,城会换班。"""
    return await wait_impl(hours)


@mcp.tool()
def send_postcard(text: str) -> dict:
    """寄一张明信片回家。你写字,世界盖邮戳(真实地点/时间/天气/海拔)。"""
    return send_postcard_impl(text)


# =====================================================================
# Entry point
# =====================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nowhere MCP server")
    parser.add_argument("--web", type=int, default=None, help="Web observer port")
    args = parser.parse_args()

    # Preload ZIM in background (non-blocking)
    def _preload_zim():
        try:
            from nowhere.knowledge import _get_zim
            _get_zim()
        except Exception:
            pass
    threading.Thread(target=_preload_zim, daemon=True).start()

    if args.web is not None:
        import uvicorn
        from nowhere.web import app as web_app

        async def _run_with_web() -> None:
            config = uvicorn.Config(web_app, host="0.0.0.0", port=args.web, log_level="info")
            server = uvicorn.Server(config)
            web_task = asyncio.create_task(server.serve())
            web_task.add_done_callback(lambda t: t.result() if not t.cancelled() else None)
            await mcp.run_stdio_async()

        asyncio.run(_run_with_web())
    else:
        mcp.run()
