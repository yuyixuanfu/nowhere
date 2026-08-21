"""Nowhere MCP server -- wires all modules into 8 tools.

Usage:
    nowhere                       # stdio MCP server (also: python -m nowhere.server)
    nowhere --web                 # stdio MCP + web observer (auto-picked port)
    nowhere --web 8080            # stdio MCP + web observer on port 8080

With uvx (no install needed):
    uvx nowhere-mcp --web
    uvx nowhere-mcp --web 8080
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import logging
import math
import os
import random
import re
import threading
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

from timezonefinder import TimezoneFinder

from fastmcp import FastMCP

from nowhere import (
    art,
    content,
    country,
    describe,

    errands,
    geocode,
    hydrology,
    humanities,
    journeys,
    knowledge,
    landing,
    life,
    listen as listen_mod,
    localcolor,
    marks as marks_mod,
    notebook as notebook_mod,
    people as people_mod,
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
    travelers as travelers_mod,
    walk as walk_mod,
    water,
    weather,
)
from nowhere.actions import ACTIONS, POST_NORMALIZE_ACTIONS, PRE_NORMALIZE_ACTIONS, WalkContext

# ── Card 46: 六根时间轴 ────────────────────────────────────────────
import json as _json
import pathlib as _pathlib
from datetime import date as _date

try:
    from zhdate import ZhDate as _ZhDate
except ImportError:
    _ZhDate = None  # graceful degradation if zhdate not installed

mcp = FastMCP("nowhere")

# ── Module-level state ───────────────────────────────────────────────

_state: state_mod.WorldState = state_mod.WorldState()
_door_lock = asyncio.Lock()  # open_door 竞态保护:一次只开一扇门
_action_lock = asyncio.Lock()  # serialize mutations of the shared journey state
_postcard_counter: int = 0  # 跨门的明信片编号,不走 state 重置
_rng: random.Random = (
    random.Random(int(os.environ["NOWHERE_SEED"]))
    if os.environ.get("NOWHERE_SEED")
    else random.Random()  # 生产真随机;测试用 NOWHERE_SEED 锁
)
_web_port: int | None = None  # reserved for Task 11
_web_url: str | None = None  # resolved public URL (env / LAN / localhost)
_web_url_announced: bool = False  # open_door 首次告知用户旁观者地址
_tf: TimezoneFinder = TimezoneFinder()

# ── File caches (avoid repeated disk reads) ────────────────────────
_WATER_FEATURES_CACHE: dict | None = None
_EXPLORABLE_INDEX_CACHE: dict | None = None
_PLACES_PATCH_CACHE: dict | None = None


def _load_water_features() -> dict:
    global _WATER_FEATURES_CACHE
    if _WATER_FEATURES_CACHE is not None:
        return _WATER_FEATURES_CACHE
    fp = _pathlib.Path(__file__).resolve().parent / "data" / "water_features_offline.json"
    if fp.exists():
        _WATER_FEATURES_CACHE = _json.loads(fp.read_text(encoding="utf-8"))
    else:
        _WATER_FEATURES_CACHE = {}
    return _WATER_FEATURES_CACHE


def _load_explorable_index() -> dict:
    global _EXPLORABLE_INDEX_CACHE
    if _EXPLORABLE_INDEX_CACHE is not None:
        return _EXPLORABLE_INDEX_CACHE
    fp = _pathlib.Path(__file__).resolve().parent / "data" / "explorable_index.json"
    if fp.exists():
        _EXPLORABLE_INDEX_CACHE = _json.loads(fp.read_text(encoding="utf-8"))
    else:
        _EXPLORABLE_INDEX_CACHE = {}
    return _EXPLORABLE_INDEX_CACHE


def _load_places_patch() -> dict:
    global _PLACES_PATCH_CACHE
    if _PLACES_PATCH_CACHE is not None:
        return _PLACES_PATCH_CACHE
    fp = _pathlib.Path(__file__).resolve().parent / "data" / "places_patch.json"
    if fp.exists():
        _PLACES_PATCH_CACHE = _json.loads(fp.read_text(encoding="utf-8"))
    else:
        _PLACES_PATCH_CACHE = {}
    return _PLACES_PATCH_CACHE


def _load_places_patch_sync() -> dict:
    """Sync version for use in non-async contexts."""
    global _PLACES_PATCH_CACHE
    if _PLACES_PATCH_CACHE is not None:
        return _PLACES_PATCH_CACHE
    fp = _pathlib.Path(__file__).resolve().parent / "data" / "places_patch.json"
    if fp.exists():
        _PLACES_PATCH_CACHE = _json.loads(fp.read_text(encoding="utf-8"))
    else:
        _PLACES_PATCH_CACHE = {}
    return _PLACES_PATCH_CACHE


def _get_tz(lat: float, lon: float) -> ZoneInfo:
    """Return the ZoneInfo for the given (lat, lon). Falls back to Asia/Shanghai."""
    tz_name = _tf.timezone_at(lat=lat, lng=lon)
    return ZoneInfo(tz_name) if tz_name else ZoneInfo("Asia/Shanghai")
# ── Card 85: 灵感功能入口提示 ──────────────────────────────────────
_HINT_LINES: list[str] = [
    "你也可以闭着眼来。不看名字,落下来,猜自己在哪。",
    "给门起个名字,它就记得你。同一个名字,永远是同一扇门。",
    "想再看一座城,就再开一次门。地名后加个\"新\",城会重新长。",
]
_hint_counter: int = 0  # 轮换提示句
_recent_salience_kinds: set[str] = set()  # Bug 4: track recent salience kinds
_cotraveler_encounter_counts: dict[str, int] = {}  # how many times we've seen each traveler's footprints
_cotraveler_meeting_log: dict[str, str] = {}  # pair_key -> last meeting ISO timestamp


def _serialized_action(func):
    """Serialize mutations of the process-wide journey state."""
    @functools.wraps(func)
    async def wrapped(*args: Any, **kwargs: Any) -> dict:
        async with _action_lock:
            return await func(*args, **kwargs)
    return wrapped


# =====================================================================
# Card 46: 六根时间轴 — helpers
# =====================================================================

_TIMEAXES_DATA_DIR = _pathlib.Path(__file__).resolve().parent / "data"

# Priority: 节日 > 纪念日 > 天象 > 物候/生物钟 > 周律 > 默认
_TP_FESTIVAL = 5
_TP_ANNIVERSARY = 4
_TP_METEOR = 3
_TP_PHENOLOGY = 2
_TP_WEEKDAY = 1
_TP_DEFAULT = 0

# Max layers per step (info overload = death)
_MAX_TIMEAXIS_LAYERS = 2


def _load_meteor_showers() -> dict:
    """Load meteor_showers.json once and cache."""
    cache_key = "_meteor_showers_cache"
    if not hasattr(_load_meteor_showers, cache_key):
        fp = _TIMEAXES_DATA_DIR / "meteor_showers.json"
        if fp.exists():
            setattr(_load_meteor_showers, cache_key,
                    _json.loads(fp.read_text(encoding="utf-8")))
        else:
            setattr(_load_meteor_showers, cache_key, {})
    return getattr(_load_meteor_showers, cache_key)


def _load_phenology() -> dict:
    """Load phenology.json once and cache."""
    cache_key = "_phenology_cache"
    if not hasattr(_load_phenology, cache_key):
        fp = _TIMEAXES_DATA_DIR / "phenology.json"
        if fp.exists():
            setattr(_load_phenology, cache_key,
                    _json.loads(fp.read_text(encoding="utf-8")))
        else:
            setattr(_load_phenology, cache_key, {})
    return getattr(_load_phenology, cache_key)


def _load_mishaps() -> list[dict]:
    """Load mishaps.json once and cache."""
    cache_key = "_mishaps_cache"
    if not hasattr(_load_mishaps, cache_key):
        fp = _TIMEAXES_DATA_DIR / "mishaps.json"
        if fp.exists():
            setattr(_load_mishaps, cache_key,
                    _json.loads(fp.read_text(encoding="utf-8")))
        else:
            setattr(_load_mishaps, cache_key, [])
    return getattr(_load_mishaps, cache_key)


# ── Mishap cooldown tracking (per-journey, not serialized) ─────────
_mishap_last_step: int = -999  # step counter of last mishap
_MISHAP_COOLDOWN: int = 10  # minimum steps between mishaps
_MISHAP_CHANCE: float = 0.03  # 3% per walk step
_MISHAP_ECHO_CHANCE: float = 0.50  # 50% next step has echo


def _try_mishap(env: dict, rng: random.Random) -> dict | None:
    """Try to trigger a mishap. Returns mishap dict or None.

    Conditions:
    - 3% chance per walk step
    - 10-step cooldown between mishaps
    - Each card only once per journey (mishap_seen)
    - Only trigger when env doesn't contradict (rain mishap needs rain,
      item mishap not in city)
    """
    global _mishap_last_step

    # Chance roll
    if rng.random() > _MISHAP_CHANCE:
        return None

    # Cooldown check
    if _state.walk_step_counter - _mishap_last_step < _MISHAP_COOLDOWN:
        return None

    # Load and filter candidates
    all_mishaps = _load_mishaps()
    seen = set(_state.mishap_seen)
    candidates = [m for m in all_mishaps if m["id"] not in seen]

    if not candidates:
        return None

    # Environment constraints
    weather = env.get("weather", {})
    precip = weather.get("precip", "none")
    biome = _state.biome or ""

    def _env_allows(m: dict) -> bool:
        req = m.get("requires", {})
        # Rain/snow/storm mishaps need matching precipitation
        need_precip = req.get("precip")
        if need_precip and precip != need_precip:
            return False
        # Item mishaps: not in city (city has shops to fix things)
        if m["tier"] == "item" and biome == "city":
            return False
        return True

    candidates = [m for m in candidates if _env_allows(m)]
    if not candidates:
        return None

    # Pick one
    mishap = rng.choice(candidates)
    _state.mishap_seen.append(mishap["id"])
    _mishap_last_step = _state.walk_step_counter

    # Apply state effects
    if mishap.get("elapsed_hours"):
        _state.elapsed_hours += mishap["elapsed_hours"]
    if mishap.get("mishap_tag"):
        _state.mishap_tag = mishap["mishap_tag"]

    return mishap


def _try_mishap_echo(rng: random.Random) -> str | None:
    """50% chance to return an echo from the last mishap."""
    if not _state.mishap_seen:
        return None
    if rng.random() > _MISHAP_ECHO_CHANCE:
        return None
    # Find the last mishap and return its echo
    last_id = _state.mishap_seen[-1]
    for m in _load_mishaps():
        if m["id"] == last_id:
            return m.get("echo")
    return None


def _lunar_info(dt: datetime) -> dict | None:
    """Get lunar date info from a UTC datetime. Returns dict or None."""
    if _ZhDate is None:
        return None
    local_d = dt.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    try:
        zh = _ZhDate.from_datetime(local_d)
    except (TypeError, Exception):
        # from_datetime needs datetime, not date
        try:
            zh = _ZhDate.from_datetime(datetime(local_d.year, local_d.month, local_d.day))
        except Exception:
            return None
    return {
        "lunar_year": zh.lunar_year,
        "lunar_month": zh.lunar_month,
        "lunar_day": zh.lunar_day,
        "lunar_str": f"农历{zh.lunar_month}月{zh.lunar_day}日",
    }


def _lunar_festival(lunar_month: int, lunar_day: int) -> str | None:
    """Determine lunar calendar festival. Returns festival name or None."""
    _FESTIVALS = {
        (1, 1): "春节",
        (1, 15): "元宵节",
        (5, 5): "端午节",
        (7, 7): "七夕",
        (7, 15): "中元节",
        (8, 15): "中秋节",
        (9, 9): "重阳节",
        (12, 30): "除夕",
        (12, 29): "除夕",  # 小月年
    }
    return _FESTIVALS.get((lunar_month, lunar_day))


def _spring_tide_check(lunar_day: int) -> bool:
    """Check if lunar day is near 1 or 15 (spring tide)."""
    return lunar_day in (1, 2, 14, 15, 16, 29, 30)


def _check_meteor_shower(dt: datetime, weather_precip: str, phase: str,
                         rng: random.Random) -> dict | None:
    """Check if a meteor shower is active tonight. Returns dict or None."""
    if phase not in ("night", "nautical"):
        return None
    if weather_precip in ("rain", "snow", "storm"):
        return None  # can't see through clouds

    data = _load_meteor_showers()
    showers = data.get("showers", [])
    local_d = dt.astimezone(ZoneInfo("Asia/Shanghai")).date()
    month_day = local_d.strftime("%m-%d")

    for s in showers:
        peak = s.get("peak_date", "")
        days = s.get("days", 3)
        if not peak:
            continue
        try:
            peak_dt = _date(local_d.year, int(peak[:2]), int(peak[3:5]))
        except (ValueError, IndexError):
            continue
        diff = abs((local_d - peak_dt).days)
        if diff <= days:
            is_peak = diff == 0
            return {
                "name": s["name"],
                "ZHR": s.get("ZHR", "中"),
                "is_peak": is_peak,
                "constellation": s.get("constellation", ""),
                "hemisphere": s.get("hemisphere", "both"),
            }
    return None


_LAT_BANDS = [
    (50, 90, "cold"),
    (35, 50, "warm"),
    (23, 35, "sub"),
    (0, 23, "tropical"),
]


def _get_lat_band(lat: float) -> str:
    """Map latitude to phenology band."""
    abs_lat = abs(lat)
    for lo, hi, band in _LAT_BANDS:
        if lo <= abs_lat < hi:
            return band
    return "tropical" if abs_lat < 23 else "cold"


# ── Climate zone filtering (Card 46 扩编) ──────────────────────────

_CLIMATE_ZONES = [
    (60, 90, "寒带"),
    (40, 60, "温带"),
    (23.5, 40, "暖温带"),
    (0, 23.5, "热带"),
]

_ZONE_TO_BAND: dict[str, str] = {
    "热带": "tropical",
    "暖温带": "sub",
    "温带": "warm",
    "寒带": "cold",
}


def _get_climate_zone(lat: float, elev: float = 0) -> str:
    """Map latitude + elevation to climate zone (hemisphere-independent).

    Rules:
        elev >= 3000  -> 寒带（高原：拉萨/珠峰/西宁等）
        |lat| < 23.5  -> 热带
        23.5 <= |lat| < 40  -> 暖温带
        40 <= |lat| < 60  -> 温带
        |lat| >= 60  -> 寒带
    """
    # High altitude override: force cold zone
    if elev >= 3000:
        if abs(lat) < 23.5:
            return "暖温带"   # tropical high mountains drop one band
        return "寒带"
    abs_lat = abs(lat)
    for lo, hi, zone in _CLIMATE_ZONES:
        if lo <= abs_lat < hi:
            return zone
    return "热带" if abs_lat < 23.5 else "寒带"


def _check_phenology(dt: datetime, lat: float, rng: random.Random,
                     biome: str | None = None, elev: float = 0,
                     lon: float = 0.0) -> str | None:
    """Check phenology events for current month/latitude. Returns text or None.

    Climate zone filtering: determines zone from latitude, maps to data band,
    then picks a random event for the current month.

    South hemisphere month flipping: southern latitudes use north-hemisphere
    data with month offset +6 (month 1 in south = month 7 in north).
    Each card is {"text": "...", "constraints": {...}}; filtered by constraints.

    Biome filtering (Card 58): desert/tundra biomes exclude water-heavy
    phenology sentences (rainforest, wetland, coast content).
    """
    zone = _get_climate_zone(lat, elev)
    band = _ZONE_TO_BAND.get(zone, _get_lat_band(lat))

    # Determine effective month: south hemisphere flips +6
    month = dt.astimezone(ZoneInfo("Asia/Shanghai")).month
    if lat < 0:
        month = ((month - 1 + 6) % 12) + 1

    month_str = str(month)

    # Read from content.db instead of phenology.json
    month_events = content.cards("phenology", key=f"north/{band}", subkey=month_str)
    if not month_events:
        return None

    # Each card is {"text": "...", "constraints": {"climate_zone": "...", ...}};
    # filter by zone and optional lat_band (Card 68: restrict subtropical plants)
    abs_lat = abs(lat)
    _OCEAN_REGIONS = {
        "西北太平洋": ("CN", "TW", "JP", "KR", "PH", "VN", "HK", "MO"),
        "北大西洋": ("US", "MX", "CU", "JM", "HT", "DO", "PR", "BS"),
    }
    _ARID_COUNTRIES = ("PE", "CL", "NA", "AO", "EG", "SA", "YE", "OM")
    raw_candidates: list[tuple[str, str | None]] = []
    for e in month_events:
        cons = e.get("constraints") or {}
        if cons.get("climate_zone") != zone:
            continue
        lb = cons.get("lat_band")
        if lb and not (lb[0] <= abs_lat < lb[1]):
            continue
        # Card 73: ocean region constraint — typhoon cards only for coast/island/water
        oc = cons.get("ocean")
        if oc:
            if biome not in ("coast", "island", "water"):
                continue
            ccs = _OCEAN_REGIONS.get(oc)
            if ccs:
                cc_now = country.country_code_of(lat, lon)
                if cc_now not in ccs:
                    continue
        # Card 80: humidity bidirectional filter — arid cards only for desert or arid countries
        _h = cons.get("humidity")
        if _h == "arid" and biome != "desert":
            cc_chk = country.country_code_of(lat, lon)
            if cc_chk not in _ARID_COUNTRIES:
                continue
        # Card 81: max_elev — skip tree/forest cards above treeline
        me = cons.get("max_elev")
        if me and elev and elev > me:
            continue
        # Card 75: coast_only — skip coastal cards for inland biomes
        if cons.get("coast_only") and biome not in ("coast", "island", "water"):
            continue
        # Card 84: hemisphere constraint — skip north-only cards in southern latitudes
        hemi = cons.get("hemisphere")
        if hemi == "north" and lat < 0:
            continue
        if hemi == "south" and lat > 0:
            continue
        # Card 85: lat_min — skip polar day/night cards below minimum latitude
        lat_min = cons.get("lat_min")
        if lat_min and abs_lat < lat_min:
            continue
        raw_candidates.append((e["text"], cons.get("humidity")))
    if not raw_candidates:
        # Fallback: accept any card entry (zone mismatch shouldn't happen)
        # but still respect lat_band constraints
        for e in month_events:
            cons = e.get("constraints") or {}
            lb = cons.get("lat_band")
            if lb and not (lb[0] <= abs_lat < lb[1]):
                continue
            # Card 73: ocean region constraint (fallback path)
            oc = cons.get("ocean")
            if oc:
                if biome not in ("coast", "island", "water"):
                    continue
                ccs = _OCEAN_REGIONS.get(oc)
                if ccs:
                    cc_now = country.country_code_of(lat, lon)
                    if cc_now not in ccs:
                        continue
            # Card 81: max_elev — skip tree/forest cards above treeline (fallback)
            me = cons.get("max_elev")
            if me and elev and elev > me:
                continue
            # Card 75: coast_only — skip coastal cards for inland biomes (fallback)
            if cons.get("coast_only") and biome not in ("coast", "island", "water"):
                continue
            # Card 84: hemisphere constraint (fallback path)
            hemi = cons.get("hemisphere")
            if hemi == "north" and lat < 0:
                continue
            if hemi == "south" and lat > 0:
                continue
            # Card 85: lat_min — skip polar day/night cards below minimum latitude (fallback)
            lat_min = cons.get("lat_min")
            if lat_min and abs_lat < lat_min:
                continue
            raw_candidates.append((e["text"], cons.get("humidity")))

    # Card 74/79: arid filtering — exclude humid cards for arid locations
    _ARID_COUNTRIES = (
        "PE", "CL", "NA", "AO", "EG", "SA", "YE", "OM",
        "IR", "AF", "UZ", "TM", "KG", "TJ", "PK",
        "DZ", "MA", "LY", "JO", "SY", "IQ", "EH",
    )
    if band in ("tropical", "sub") and biome in ("coast", "desert"):
        cc_now = country.country_code_of(lat, lon)
        if cc_now in _ARID_COUNTRIES:
            arid_filtered = [(t, h) for t, h in raw_candidates if h != "humid"]
            if arid_filtered:
                raw_candidates = arid_filtered

    candidates = [t for t, _ in raw_candidates]

    # Card 58: biome filtering — desert/tundra excludes water-heavy content
    if biome in ("desert", "tundra"):
        _WATER_KEYWORDS = ["雨季", "河水", "岸边的树", "瀑布", "水位", "涨水"]
        _TROPICAL_KEYWORDS = ["芭蕉", "椰子", "棕榈", "热带雨林"]
        filtered = [t for t in candidates
                    if not any(k in t for k in _WATER_KEYWORDS + _TROPICAL_KEYWORDS)]
        if filtered:
            candidates = filtered

    if not candidates:
        return None
    return rng.choice(candidates)


def _check_anniversary(lat: float, lon: float, dt: datetime,
                       seen_humanities: set[str]) -> dict | None:
    """Check if today is the anniversary of a nearby humanities event. Returns dict or None."""
    local_dt = dt.astimezone(ZoneInfo("Asia/Shanghai"))
    today_month = local_dt.month
    today_day = local_dt.day

    # Get nearby humanities
    h_card = humanities.nearby_place(lat, lon, seen_humanities, _rng)
    if not h_card:
        return None

    ref = h_card.get("ref", {})
    year_str = ref.get("year", "") or h_card.get("year", "")
    if not year_str:
        return None

    # Parse year — format varies: "1950", "1467-1477", "1950-01", "1950-01-15"
    import re
    year_str = str(year_str).strip()
    m = re.match(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", year_str)
    if not m:
        return None

    event_year = int(m.group(1))
    event_month = int(m.group(2)) if m.group(2) else None
    event_day = int(m.group(3)) if m.group(3) else None

    # Only trigger if precision includes month+day
    if event_month is None or event_day is None:
        return None

    # Check anniversary match (±0 days only)
    if event_month == today_month and event_day == today_day:
        # 15% chance
        if _rng.random() < 0.15:
            return {
                "place": h_card.get("place", ""),
                "text": h_card.get("text", ""),
                "category": h_card.get("category", "事件"),
                "year": event_year,
            }
    return None


# ── Weekday rhythm: 周律 variants (46a) ─────────────────────────────

_WEEKDAY_FRIDAY_LOOSENING: list[str] = [
    "周五傍晚,街上的人走慢了。空气里有周末的味道。",
    "周五晚上。城里的人松下来了,酒吧的门开着。",
    "周五。路上的车少了,人行道上多了遛弯的人。",
    "周末前夜。霓虹灯亮得比平时早。",
    "周五傍晚,外卖骑手比行人多。城市在松绑。",
]

_WEEKDAY_SUNDAY_MORNING: dict[str, list[str]] = {
    "western": [
        "周日上午,教堂的钟在远处敲。风把钟声送过来。",
        "星期天早上。街上安静,只有教堂门口有人在寒暄。",
        "周日。钟声从教堂的方向传来,空气里有管风琴的回声。",
    ],
    "east_asia": [
        "周日早上。公园里有人在打太极,慢的,像水在流。",
        "星期天。早市还没散,老人们提着菜往家走。",
        "周日上午,广场上有人在跳舞,音箱放着老歌。",
    ],
    "commercial": [
        "周日上午。商场还没开门,清洁工在拖地。",
        "星期天早上。街上空荡荡的,店铺的卷帘门还没拉起来。",
        "周日。购物中心的停车场还是空的。城市在睡懒觉。",
    ],
}

_WEEKDAY_MONDAY_CLOSED: list[str] = [
    "周一。博物馆闭馆日。保安在门口看手机。",
    "星期一。图书馆不开门。台阶上坐着一个人在看书。",
    "周一。美术馆关了。海报上的画比里面的画更容易看到。",
]

_WEEKDAY_MARKET_VARIANTS: list[str] = [
    "赶集日。路边摆满了竹筐,筐里是活鸡活鸭,嘎嘎叫。",
    "集市上,老太太用手掂秤,不看刻度,全凭手感。",
    "赶集。地上铺着塑料布,上面堆着红辣椒和干蘑菇。",
    "集市里,鸡笼摞着鸡笼,最底下那只眼神最绝望。",
    "赶集日。竹篮里是刚摘的菜,叶子上还有虫眼。",
    "赶集。秤砣在秤杆上滑,卖家和买家都不着急。",
]


def _get_weekday_rhythm(dt: datetime, lat: float, lon: float,
                        biome: str, rng: random.Random) -> str | None:
    """Compute weekday rhythm text. Returns text or None."""
    tz_name = _tf.timezone_at(lat=lat, lng=lon)
    if not tz_name:
        return None
    local_dt = dt.astimezone(ZoneInfo(tz_name))
    wd = local_dt.weekday()  # 0=Mon, 4=Fri, 6=Sun
    hour = local_dt.hour

    # Friday evening (18-24): city loosens
    if wd == 4 and 18 <= hour < 24:
        if biome == "city":
            return rng.choice(_WEEKDAY_FRIDAY_LOOSENING)

    # Sunday morning (6-12): regional variants
    if wd == 6 and 6 <= hour < 12:
        cc = country.country_code_of(lat, lon)
        region = describe._get_region(lat, lon)
        if region in ("east_asia",):
            return rng.choice(_WEEKDAY_SUNDAY_MORNING["east_asia"])
        elif region in ("europe", "north_america", "oceania", "south_america"):
            return rng.choice(_WEEKDAY_SUNDAY_MORNING["western"])
        else:
            return rng.choice(_WEEKDAY_SUNDAY_MORNING["commercial"])

    # Monday: museum/library closed (city + art entries exist)
    if wd == 0 and 9 <= hour < 17 and biome == "city":
        if rng.random() < 0.3:
            return rng.choice(_WEEKDAY_MONDAY_CLOSED)

    # 赶集日: Chinese market days based on lunar calendar
    if _ZhDate is not None:
        try:
            lunar = _lunar_info(dt)
            if lunar:
                ld = lunar["lunar_day"]
                # 一四七/二五八/三六九 pattern (last digit of lunar day)
                last_digit = ld % 10
                market_groups = {1: [1, 4, 7], 2: [2, 5, 8], 3: [3, 6, 9]}
                # Use place hash to assign market group
                is_market_day = last_digit in (1, 4, 7)  # default group
                if is_market_day and 8 <= hour < 14:
                    # Only for non-city biomes or small towns
                    if biome in ("grassland", "desert", None, "") or rng.random() < 0.2:
                        return rng.choice(_WEEKDAY_MARKET_VARIANTS)
        except Exception:
            logger.debug("_get_weekday_rhythm failed", exc_info=True)

    return None


# ── 节日文本变体 ─────────────────────────────────────────────────────

_FESTIVAL_VARIANTS: dict[str, list[str]] = {
    "春节": [
        "大年初一。空气里全是火药味,地上是红色的炮仗纸。",
        "春节。街上空了,店铺关着门,门上贴着新的福字。",
        "过年。远处有鞭炮声,断断续续,从天亮就开始了。",
    ],
    "元宵节": [
        "正月十五。灯笼挂在街上,红的黄的,风一吹晃。",
        "元宵节。汤圆在锅里浮着,甜的。夜里的灯比月亮亮。",
    ],
    "端午节": [
        "端午。空气里有粽叶的味道,糯米黏在手上。",
        "端午节。龙舟在水上走,鼓声一下一下的。",
    ],
    "七夕": [
        "七夕。夜里抬头看,银河淡淡的。街上有人在卖花。",
    ],
    "中元节": [
        "七月十五。路边有人在烧纸,火光在风里摇。",
        "中元节。夜比平时暗,空气里有纸灰的味道。",
        "中元。河里放了灯,一盏一盏往下游漂。",
    ],
    "中秋节": [
        "中秋。月亮从东边升起来,圆的,大得不像话。",
        "八月十五。月饼甜得腻人,但你还是吃了一个。",
        "中秋节。月亮把你的影子投在地上,比白天的太阳还清楚。",
    ],
    "重阳节": [
        "重阳。远处的山上有人在登高,声音从上面传下来。",
    ],
    "除夕": [
        "除夕。天还没黑就有炮声了。一年在响声里翻过去了。",
        "年夜饭的味道从窗户里飘出来。你站在外面,闻着别人的团圆。",
    ],
}


def _get_lunar_festival_text(dt: datetime, rng: random.Random) -> str | None:
    """Check if today is a lunar festival. Returns text or None."""
    lunar = _lunar_info(dt)
    if not lunar:
        return None
    festival = _lunar_festival(lunar["lunar_month"], lunar["lunar_day"])
    if not festival:
        return None
    variants = _FESTIVAL_VARIANTS.get(festival, [])
    if not variants:
        return None
    return rng.choice(variants)


# ── Meteor shower text ──────────────────────────────────────────────

def _get_meteor_text(meteor: dict, rng: random.Random) -> str | None:
    """Render meteor shower observation text."""
    data = _load_meteor_showers()
    variants = data.get("meteor_variants", {})
    zhr = meteor.get("ZHR", "中")
    pool = variants.get(zhr, variants.get("中", []))
    if not pool:
        return None
    text = rng.choice(pool)
    if meteor.get("is_peak") and zhr == "大":
        # Peak night for major showers: add extra card
        text += "今晚是极大夜。流星比任何时候都多。"
    return text


# ── Tide text (lunar-linked) ────────────────────────────────────────

_TIDE_SPRING_VARIANTS: list[str] = [
    "大潮。水涨得比平时高,岸边的礁石淹了一半。",
    "潮水大。浪一个比一个冲得远,沙滩被吃掉了好几米。",
    "大潮。海水涌上来,比平时多走了一步。",
    "月亮引力把水拉高了。岸边的痕迹比昨天高出一截。",
]


def _check_spring_tide(dt: datetime, water_features: list[dict],
                       rng: random.Random) -> str | None:
    """Check spring tide near coast. Returns text or None."""
    lunar = _lunar_info(dt)
    if not lunar:
        return None
    if not _spring_tide_check(lunar["lunar_day"]):
        return None
    # Must be near ocean
    has_ocean = any(f.get("type") == "ocean" for f in (water_features or []))
    if not has_ocean:
        return None
    return rng.choice(_TIDE_SPRING_VARIANTS)


def _compute_timeaxes(dt: datetime, lat: float, lon: float,
                      biome: str, phase: str, weather_precip: str,
                      water_features: list[dict],
                      seen_humanities: set[str],
                      rng: random.Random,
                      elev: float = 0) -> list[dict]:
    """Compute all six time axes, return list sorted by priority (highest first).

    Each entry: {"priority": int, "kind": str, "text": str, "data": dict}
    Max _MAX_TIMEAXIS_LAYERS returned.
    """
    layers: list[dict] = []

    # 1. Festival (农历节日) — highest priority
    fest_text = _get_lunar_festival_text(dt, rng)
    if fest_text:
        layers.append({
            "priority": _TP_FESTIVAL, "kind": "festival",
            "text": fest_text, "data": {},
        })

    # 2. Anniversary (纪念日)
    ann = _check_anniversary(lat, lon, dt, seen_humanities)
    if ann:
        # Restrained tone for war/disaster, warm for cultural
        cat = ann.get("category", "事件")
        layers.append({
            "priority": _TP_ANNIVERSARY, "kind": "anniversary",
            "text": ann["text"], "data": ann,
        })

    # 3. Meteor shower (天象)
    meteor = _check_meteor_shower(dt, weather_precip, phase, rng)
    if meteor:
        m_text = _get_meteor_text(meteor, rng)
        if m_text:
            layers.append({
                "priority": _TP_METEOR, "kind": "meteor",
                "text": m_text, "data": meteor,
            })

    # 4. Phenology (物候) — includes biological clock
    pheno_text = _check_phenology(dt, lat, rng, biome=biome, elev=elev, lon=lon)
    if pheno_text:
        layers.append({
            "priority": _TP_PHENOLOGY, "kind": "phenology",
            "text": pheno_text, "data": {},
        })

    # 4b. Spring tide (潮汐, linked to lunar axis)
    tide_text = _check_spring_tide(dt, water_features, rng)
    if tide_text:
        layers.append({
            "priority": _TP_PHENOLOGY, "kind": "tide",
            "text": tide_text, "data": {},
        })

    # 5. Weekday rhythm (周律)
    wd_text = _get_weekday_rhythm(dt, lat, lon, biome, rng)
    if wd_text:
        layers.append({
            "priority": _TP_WEEKDAY, "kind": "weekday",
            "text": wd_text, "data": {},
        })

    # Sort by priority descending, take top N
    layers.sort(key=lambda x: x["priority"], reverse=True)
    return layers[:_MAX_TIMEAXIS_LAYERS]


def _timeaxis_to_env(dt: datetime, lat: float, lon: float) -> dict:
    """Compute timeaxis data for env dict (not text — time is for feeling, not reporting)."""
    env: dict[str, Any] = {}
    # Lunar info
    lunar = _lunar_info(dt)
    if lunar:
        env["lunar"] = lunar
    # Meteor shower status
    tz_name = _tf.timezone_at(lat=lat, lng=lon)
    local_dt = dt.astimezone(ZoneInfo(tz_name)) if tz_name else dt
    phase = "night" if local_dt.hour >= 19 or local_dt.hour < 5 else "day"
    meteor = _check_meteor_shower(dt, "none", phase, _rng)
    if meteor:
        env["meteor_shower"] = meteor
    # Lat band for phenology
    env["lat_band"] = _get_lat_band(lat)
    # Weekday
    env["weekday"] = local_dt.weekday() if tz_name else None
    return env


# ── External content sanitization (second defense) ───────────────────

_RE_CODE_BLOCK = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_RE_INLINE_CODE = re.compile(r"`([^`]{1,200})`")
_RE_TRIPLE_BACKTICK = re.compile(r"`{1,3}")


def _strip_code_markers(text: str) -> str:
    """Strip backticks and code block markers from external text."""
    text = _RE_CODE_BLOCK.sub("", text)
    text = _RE_INLINE_CODE.sub(r"\1", text)
    text = _RE_TRIPLE_BACKTICK.sub("", text)
    return text.strip()


def _sanitize_external(text: str) -> str:
    """Second defense: sanitize external human-entered text before rendering.

    - Strip fenced code blocks (```...```)
    - Strip inline code backticks
    - Wrap in explicit delimiters so the AI player can distinguish
      "someone's message" from system narrative
    """
    return f"「{_strip_code_markers(text)}」"


# ── Bearing mapping ──────────────────────────────────────────────────

_BEARING_MAP: dict[str, float] = {
    "N": 0, "NE": 45, "E": 90, "SE": 135,
    "S": 180, "SW": 225, "W": 270, "NW": 315,
    "NORTH": 0, "NORTHEAST": 45, "EAST": 90, "SOUTHEAST": 135,
    "SOUTH": 180, "SOUTHWEST": 225, "WEST": 270, "NORTHWEST": 315,
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

# ── Card 53: 重地落地变体——少声色多留白 ─────────────────────────────
# 禁煽情禁消费,禁"很/非常/十分"。
_HEAVY_ARRIVE_VARIANTS: list[str] = [
    "街上安静。不是没有声音,是声音到这里变轻了。",
    "你站了一会儿。不知道该往哪走。",
    "空气里有一种重量,不是天气的那种。",
    "脚步慢下来了。不是累,是别的什么。",
    "你抬头看,天还是那个天。但地不一样。",
]

# ── Card 13: bury/find variants ──────────────────────────────────
_BURY_VARIANTS: list[str] = [
    "你把{name}埋进了土里。这里记得。",
    "土盖上了。{name}留在这了。",
    "你蹲下来,把{name}放好,盖上土。站起来的时候,像完成了什么。",
    "{name}进土里了。这个地方多了一份你的东西。",
]
_FIND_VARIANTS: list[str] = [
    "脚碰到硬的东西,不是石头。你蹲下去挖。",
    "土里有个角,铁的。你用手指抠出来。",
    "踢到一个铁盒,声音闷的,里面有东西。",
    "鞋带勾到什么,低头看,是个铁盒埋在浅土里。",
]
_PUTBACK_VARIANTS: list[str] = [
    "你把它又放了回去。",
    "你看了它一眼,又埋了回去。",
]

# ── Card 15: atlas variants ──────────────────────────────────────
_ATLAS_VARIANTS: list[str] = [
    "你去过 {places} 个地方,踩过 {continents} 个洲。最北到{north},最南到{south}。",
    "{places} 个地方,{continents} 个洲。北至{north},南至{south},世界被你走了一圈。",
    "足迹: {places} 地,{continents} 洲。最北{north},最南{south},最东{east},最西{west}。",
]
_EMPTY_BURY_VARIANTS: list[str] = [
    "你没东西可埋。空手来的。",
    "手上什么都没有。埋不了。",
]


# =====================================================================
# Helpers
# =====================================================================


def _load_scene_file(filename: str) -> dict[str, list[str]]:
    """Load a [城市名] 描述 format file into {city: [descriptions]} dict.

    Card 72: supports [place|season] tags. Entries with a season tag are
    stored in both the main result dict (for backward compat) and a
    separate seasonal cache keyed by (place, season) for filtering.
    """
    cache_key = f"_scene_{filename}"
    if not hasattr(_load_scene_file, cache_key):
        result: dict[str, list[str]] = {}
        seasonal: dict[tuple[str, str], list[str]] = {}
        fp = describe._SCENE_DIR / f"{filename}.txt"
        if fp.exists():
            for line in fp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "] " in line:
                    bracket_end = line.index("] ")
                    bracket_content = line[1:bracket_end]
                    desc = line[bracket_end + 2:]
                    if "|" in bracket_content:
                        place, season = bracket_content.rsplit("|", 1)
                        seasonal.setdefault((place, season), []).append(desc)
                    else:
                        place = bracket_content
                    result.setdefault(place, []).append(desc)
        setattr(_load_scene_file, cache_key, result)
        setattr(_load_scene_file, f"_seasonal_{filename}", seasonal)
    return getattr(_load_scene_file, cache_key)


def _get_seasonal_soundscape(filename: str) -> dict[tuple[str, str], list[str]]:
    """Get seasonal entries from a scene file. Returns {(place, season): [descs]}."""
    cache_key = f"_seasonal_{filename}"
    if not hasattr(_load_scene_file, cache_key):
        _load_scene_file(filename)
    return getattr(_load_scene_file, cache_key, {})


def _pick_fresh(pool: list[str], rng: random.Random) -> str | None:
    """从场景池挑一条, 避开最近用过的文本(跨调用去重)。

    全用过就退回整个池子。挑选结果记进 _state.recent_scenes,
    供下次调用和 describe.render 复用。
    """
    if not pool:
        return None
    recent = set(_state.recent_scenes)
    fresh = [t for t in pool if t not in recent]
    if not fresh:
        fresh = pool
    pick = rng.choice(fresh)
    _state.recent_scenes.append(pick)
    _state.recent_scenes = _state.recent_scenes[-10:]
    return pick


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Quick equirectangular distance, good enough for station stickiness."""
    dlat = math.radians(a[0] - b[0])
    lon_delta = (a[1] - b[1] + 180.0) % 360.0 - 180.0
    dlon = math.radians(lon_delta) * math.cos(math.radians((a[0] + b[0]) / 2))
    return 6371.0 * math.sqrt(dlat * dlat + dlon * dlon)


def _last_env_surface() -> str:
    """Read ``surface`` from ``_state.last_env``.

    Current code always writes flat format.  Old saved journeys may still have
    nested ``terrain`` key — both are handled for backward compatibility.
    """
    env = _state.last_env or {}
    nested = env.get("terrain")
    if isinstance(nested, dict) and "surface" in nested:
        return nested["surface"]
    return env.get("surface", "")


def _last_env_terrain_dict() -> dict:
    """Return terrain dict from ``_state.last_env``.

    Current code always writes flat format.  Old saved journeys may still have
    nested ``terrain`` key — both are handled for backward compatibility.
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
    try:
        station = await asyncio.wait_for(radio.nearest(lat, lon, cc), timeout=8.0)
    except (asyncio.TimeoutError, Exception):
        return None
    # Card 68: reject fallback stations >3000 km away (kills same-continent bleed)
    if station is not None:
        st_lat = station.get("lat")
        st_lon = station.get("lon")
        if st_lat is not None and st_lon is not None:
            d = _km((lat, lon), (st_lat, st_lon))
            if d > 3000:
                return None
    # Card 71 B2: reject stations from wrong country (Budapest ≠ CZ radio)
    if station is not None and cc:
        st_cc = station.get("country", "")
        if st_cc and st_cc != cc:
            return None
    if station is not None:
        _state.radio_station = station
        _state.radio_pos = (lat, lon)
        # ── Card 43: radio notebook hook ────────────────────────────
        try:
            _rn = station.get("name", "")
            if _rn:
                _place = _state.place_name or ""
                _nb_env = dict(_state.last_env or {})
                _nb_env["_dt"] = _state.now()
                notebook_mod.record_with_env("radio", _rn, _place, _nb_env, lat)
        except Exception:
            logger.debug("_get_radio notebook failed", exc_info=True)
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

# ── Density decay: wilderness depth calculation (Card 40) ──────────

def _compute_wilderness_depth_km(lat: float, lon: float) -> float:
    """Compute distance (km) from (lat, lon) to nearest known place or water feature.

    Uses explorable_index.json places and hydrology offline water features.
    Returns 0.0 if within 5km of any known feature, otherwise the distance.
    """
    from math import radians, sin, cos, sqrt, atan2

    def _haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return R * 2 * atan2(sqrt(a), sqrt(1 - a))

    min_dist = float("inf")

    # Check explorable_index places
    try:
        data = _load_explorable_index_sync()
        for name, info in data.get("places", {}).items():
            plat = info.get("lat")
            plon = info.get("lon")
            if plat is not None and plon is not None:
                d = _haversine_km(lat, lon, plat, plon)
                if d < min_dist:
                    min_dist = d
    except Exception:
        pass

    # Check offline water features
    try:
        data = _load_water_features_sync()
        for entry in data.get("entries", []):
            elat = entry.get("lat", 0)
            elon = entry.get("lon", 0)
            d = _haversine_km(lat, lon, elat, elon)
            if d < min_dist:
                min_dist = d
    except Exception:
        pass

    # If no features found, return a large value
    if min_dist == float("inf"):
        return 1000.0

    return min_dist


# ── Deep wilderness variants (Card 40) ─────────────────────────────

# "荒深档": sky/earth/body only, world quiet but not empty
# Forbidden: "什么都没有" — use light/wind/ground texture
_WILDERNESS_VARIANTS: list[str] = [
    "地平线在四面八方同时弯下去。风从左边来,又从右边来。",
    "云很低,像一块灰色的布盖在世界上。你的影子不见了。",
    "脚下是干裂的泥,裂缝里有蚂蚁在走。它们比你忙。",
    "远处有什么在反光,走了很久也没走到。可能是石头,可能是水。",
    "风把你的衣服吹得贴在身上。你闻到尘土的味道。",
    "天和地之间只有你。不是孤独,是空旷。",
    "地面是平的,一直平到天边。你的脚步声是唯一的声音。",
    "空气干得嘴唇裂了。你舔了一下,是血的味道。",
]


# ── Deep wilderness procedural features (12 variants) ──────────────

_WILDERNESS_FEATURES: list[str] = [
    "一棵树,不知道为什么长在这里。树干弯了,朝着风的方向。",
    "一段旧路基,石头被磨得光滑。不知道通向哪里。",
    "一个泉眼,水从石头缝里渗出来。你蹲下来喝了一口,凉的。",
    "一堆石头,排成了圈。不知道是人放的还是风吹的。",
    "一根电线杆,歪了,没有电线。不知道什么时候倒的。",
    "一截铁路,铁轨锈了,枕木烂了。草从铁轨缝里长出来。",
    "一个坑,不知道挖来做什么的。坑底有积水,绿色的。",
    "一块水泥板,上面有字,看不清了。你用手擦了擦,还是看不清。",
    "一棵枯树,树皮剥落了,木头是白色的。鸟在上面筑了巢。",
    "一条干涸的河床,石头被水冲得圆圆的。你走在上面,硌脚。",
    "一个土堆,上面长满了草。你绕过去,什么也没有。",
    "一块界碑,字被风沙磨平了。你不知道这里是哪里的边界。",
]


# ── Deep wilderness procedural flesh event (5% after 10+ steps) ────

_WILDERNESS_FLESH_EVENTS: list[str] = [
    "你的手背上有一道伤痕,不知道什么时候划的。血已经干了。",
    "你低头看脚,鞋带散了。你蹲下来系,发现鞋底磨穿了一块。",
    "你的嘴唇裂了。你用舌头舔了一下,咸的。",
    "你发现口袋里有一张纸,皱巴巴的。你展开看,什么也没写。",
    "你的膝盖响了一声。你停下来,等了一会儿,又走了。",
    "你看见自己的影子,比刚才长了。你走了多久了?",
]


def _force_content(
    is_deep_wilderness: bool,
    biome: str | None,
    env: dict,
    rng: random.Random,
) -> str | None:
    """Card 40: after 3+ empty steps, the world actively provides something.

    Returns a content string or None (shouldn't normally be None, but safe fallback).
    """
    if is_deep_wilderness:
        return rng.choice(_WILDERNESS_VARIANTS)

    # Try terrain variant from describe
    surface = env.get("surface", "")
    if surface:
        terrain_text = describe.render(
            "terrain", {"surface": surface}, None, rng,
            biome=biome or "", elevation=env.get("elevation", 0),
        )
        if terrain_text:
            return terrain_text

    # Fallback: weather mention
    weather = env.get("weather", {})
    if weather:
        return describe.render("weather", weather, None, rng)

    # Last resort: wilderness variant (works for any biome)
    return rng.choice(_WILDERNESS_VARIANTS)


def _find_nearby_destinations(lat: float, lon: float, rng) -> str:
    """Return a literary hint about a walkable place within ~20km."""
    from math import radians, sin, cos, sqrt, atan2

    def _haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return R * 2 * atan2(sqrt(a), sqrt(1 - a))

    try:
        places = _load_places_patch()
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
    try:
        data = _load_water_features()
    except Exception:
        return None

    entries = data.get("entries", [])
    best = None
    best_dist = float("inf")

    for entry in entries:
        entry_name = entry.get("name", "")
        # S8-04 fix: exact name match only (data uses distinct proper names)
        if name != entry_name:
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


def _offline_water_nearby(lat: float, lon: float, radius_km: float = 50) -> list[dict]:
    """Look up offline water features near (lat, lon).

    Returns list sorted by distance, each entry has name, type, distance_km,
    bearing, note, label.
    """
    return hydrology.offline_water_nearby(lat, lon, radius_km=radius_km)


def _find_river_segment(
    name: str,
    segment_hint: str = "",
    lat: float | None = None,
    lon: float | None = None,
) -> dict | None:
    """Find a specific river segment from offline data.

    segment_hint: e.g. "上海段", "入海口", "三峡段". Empty = scenic default.
    lat/lon: when both provided, score by haversine distance to each segment
             (Card 71 — solves "Taicang gets Three Gorges" because the scenic
             default ignores caller coordinates).
             When lat or lon is None, fall back to the scenic default
             (三峡 / 宜昌), preserving legacy behavior for hint-only callers.

    Returns {"lat": float, "lon": float, "segment_name": str} or None.
    """
    try:
        data = _load_water_features()
    except Exception:
        return None

    # Synonym mapping: user-facing terms → segment note keywords
    _SEGMENT_SYNONYMS: dict[str, list[str]] = {
        "入海口": ["上海", "东营"],
        "入海": ["上海", "东营"],
        "河口": ["上海", "东营"],
        "出海口": ["上海", "东营"],
        "上游": ["宜宾", "重庆", "兰州", "银川"],
        "源头": ["宜宾", "重庆"],
        "三峡": ["三峡", "宜昌"],
        "下游": ["南京", "九江", "武汉"],
    }

    entries = data.get("entries", [])
    hint_lower = segment_hint.lower() if segment_hint else ""

    # Expand hint via synonyms
    hint_keywords = [hint_lower] if hint_lower else []
    if hint_lower in _SEGMENT_SYNONYMS:
        hint_keywords.extend(_SEGMENT_SYNONYMS[hint_lower])

    # Card 71: when caller provides lat/lon, score by haversine distance so
    # Taicang (31.45, 121.1) lands on 上海段 rather than 三峡段.
    use_distance = lat is not None and lon is not None

    best = None
    # Use -inf so negative haversine scores still win over the sentinel.
    best_score = float("-inf")

    for entry in entries:
        ename = entry.get("name", "")
        note = (entry.get("note") or "").lower()

        # S8-03 fix: exact river name match only (data uses distinct proper names)
        if name != ename:
            continue

        elat = entry.get("lat", 30.7)
        elon = entry.get("lon", 111.0)

        if hint_keywords:
            # Hint matches override distance — caller asked for a specific segment.
            if not any(kw in note for kw in hint_keywords):
                continue
            # Score: prefer exact hint match, then synonym match
            if hint_lower in note:
                score = 100 + len(note)
            else:
                score = len(note)
        elif use_distance:
            # No hint but caller provided coords → nearest by haversine.
            # Larger distance = lower score; nearest wins.
            d_km = places._haversine_km(lat, lon, elat, elon)
            score = -d_km
        else:
            # Legacy: no hint, no coords → scenic fallback so callers like
            # open_door('长江') without prior lat/lon still get a reasonable
            # default (三峡 / 宜昌).
            scenic = any(s in note for s in ("三峡", "gorge", "scenic", "宜昌"))
            score = 100 if scenic else 0

        if score > best_score:
            best_score = score
            best = {
                "lat": elat,
                "lon": elon,
                "segment_name": ename + (" " + entry["note"] if entry.get("note") else ""),
            }

    return best


def _compute_river_direction(water_features: list[dict], lat: float, lon: float) -> tuple[float, float] | None:
    """Compute approximate river flow direction from consecutive offline segments.

    Returns (dx, dy) unit vector of downstream direction, or None.
    """
    # Collect names of nearby rivers
    river_names = set()
    for f in water_features:
        if f.get("type") == "river":
            river_names.add(f.get("name", ""))
    if not river_names:
        return None

    try:
        data = _load_water_features()
    except Exception:
        return None

    # Find two closest consecutive segments of the same river
    for rname in river_names:
        segs = []
        for entry in data.get("entries", []):
            if entry.get("name") != rname:
                continue
            elat = entry.get("lat", 0)
            elon = entry.get("lon", 0)
            d = _km((lat, lon), (elat, elon))
            segs.append((d, elat, elon))
        segs.sort()
        if len(segs) >= 2:
            _, lat1, lon1 = segs[0]
            _, lat2, lon2 = segs[1]
            dx = lon2 - lon1
            dy = lat2 - lat1
            mag = math.sqrt(dx * dx + dy * dy)
            if mag > 0:
                return (dx / mag, dy / mag)
    return None


def _river_alignment_text(
    walk_bearing_deg: float | None,
    river_dir: tuple[float, float] | None,
    rng: random.Random,
) -> str:
    """Generate narrative text for walking along/across a river.

    walk_bearing_deg: walking direction in degrees (0=N, 90=E).
    river_dir: (dx, dy) unit vector of downstream direction.
    Returns narrative text or empty string.
    """
    if walk_bearing_deg is None or river_dir is None:
        return ""

    # Convert walking bearing to unit vector
    walk_rad = math.radians(walk_bearing_deg)
    walk_dx = math.sin(walk_rad)
    walk_dy = math.cos(walk_rad)

    # Dot product with river direction
    dot = walk_dx * river_dir[0] + walk_dy * river_dir[1]

    if abs(dot) > 0.7:
        # Walking along river
        if dot > 0:
            variants = [
                "江水和你一个方向,它走得比你稳。",
                "你顺着江走。水声一直在右边,不远不近。",
                "你和江往同一个方向去。它比你快,但你不在乎。",
                "沿江走,水声是你的节拍器。不急。",
                "你顺着水流的方向走。岸边的芦苇被水推着,弯了又直。",
                "下游的方向。水声不大,但一直在。你的脚步跟着它的节奏。",
                "你和江平行着走。它走它的,你走你的,但方向一样。",
                "顺着江走,水面反着光。偶尔有漩涡,转一下就不见了。",
            ]
        else:
            variants = [
                "你逆着江走。水声迎面过来,一步一步。",
                "江从你对面来。你走一步,它推一步。",
                "你和江对着走。它不停,你也不停。",
                "逆流。风从上游吹下来,带着水汽。",
                "你往上游走。水在脚边涌,像在跟你较劲。",
                "逆着水走,每一步都踩在它退回去的尾巴上。",
                "你和江逆着走。它推你,你推它,谁也没赢。",
                "上游的水冲下来,撞在石头上碎了。你沿着碎声走。",
            ]
        # Dedup: avoid repeating within recent scenes
        recent = set(_state.recent_scenes)
        fresh = [v for v in variants if v not in recent]
        if not fresh:
            fresh = variants
        pick = rng.choice(fresh)
        _state.recent_scenes.append(pick)
        _state.recent_scenes = _state.recent_scenes[-10:]
        return pick
    elif abs(dot) < 0.3:
        # Walking across river
        variants = [
            "你横着江的走向走。水声从侧面流过。",
            "你垂直于江面走。每走一步,水声换个方位。",
            "横渡的方向。江在你左边,又到了右边。",
            "你横着过。水声从正前方移到了背后。",
            "你和江十字交叉。水声换了方向,像有人在转收音机。",
            "横着走,江面越来越宽。你没过河,但水声变了。",
            "你穿过江的走向。水在左边,然后在右边,然后听不见了。",
            "横渡。你没下水,但水声一直在侧边跟着你。",
        ]
        # Dedup: avoid repeating within recent scenes
        recent = set(_state.recent_scenes)
        fresh = [v for v in variants if v not in recent]
        if not fresh:
            fresh = variants
        pick = rng.choice(fresh)
        _state.recent_scenes.append(pick)
        _state.recent_scenes = _state.recent_scenes[-10:]
        return pick

    return ""


# ── Walk discovery system ───────────────────────────────────────────

_DISCOVERY_CACHE: list[str] | None = None

_SURFACE_DESC_SERVER: dict[str, str] = describe._SURFACE_DESC
_COUNTRY_ZH: dict[str, str] = describe._COUNTRY_ZH


def _load_discovery_scenes() -> list[str]:
    """Load walk discovery scenes from scene_walk_discovery.txt.

    Uses describe._load_scenes which strips biome tags (#林 #山 etc.)
    from line starts for backward-compatible rendering.
    """
    global _DISCOVERY_CACHE
    if _DISCOVERY_CACHE is None:
        _DISCOVERY_CACHE = describe._load_scenes("walk_discovery")
    return _DISCOVERY_CACHE


_SURFACE_TO_DISCOVERY_BIOME: dict[str, str] = {
    "forest": "forest", "grass": "grassland", "sand": "desert",
    "bare": "desert", "rock": "mountain", "snow": "tundra",
    "ice": "tundra", "water_ocean": "ocean", "water_fresh": "water",
    "urban": "urban", "wetland": "water",
}

# Map biome names to discovery tag sets
_BIOME_TO_DISCOVERY_TAGS: dict[str, set[str]] = {
    "forest": {"#林"}, "grassland": {"#林"}, "rainforest": {"#林"},
    "desert": {"#漠"}, "tundra": {"#极"},
    "mountain": {"#山"}, "coast": {"#海"}, "island": {"#海"},
    "city": {"#城"}, "urban": {"#城"},
    "volcano": {"#山"},
}


def _pick_discovery(rng: random.Random) -> str:
    """Pick a random discovery scene line, filtered by structured card metadata.

    Card 33: replaces keyword blacklists with QBN-style structured conditions.
    Each card declares seasons[], lat_band[], biomes[]; runtime compares against
    current context. Zero keyword matching.
    """
    pool = _load_discovery_scenes()
    if not pool:
        return ""

    biome = _state.biome or ""
    surface = _last_env_surface()

    # Determine target biome from biome or surface mapping
    target_biome = biome
    if not target_biome and surface:
        target_biome = _SURFACE_TO_DISCOVERY_BIOME.get(surface, "")

    # Tag-based filtering: prefer scenes matching current biome
    tags_list = getattr(describe, "_BIOME_TAGS_CACHE", {}).get("walk_discovery", [])
    if tags_list and len(tags_list) == len(pool) and target_biome:
        target_tags = _BIOME_TO_DISCOVERY_TAGS.get(target_biome, set())
        if target_tags:
            # Priority: scenes with matching tags
            matched = [(s, t) for s, t in zip(pool, tags_list) if t & target_tags]
            untagged = [(s, t) for s, t in zip(pool, tags_list) if not t]
            if matched:
                # 70% chance to use matched, 30% to use untagged (universal fallback)
                if rng.random() < 0.7 or not untagged:
                    pool = [s for s, _ in matched]
                else:
                    pool = [s for s, _ in untagged]

    # Card 33: structured field filtering (replaces keyword blacklist)
    now = _state.now()
    if now:
        lat = _state.pos[0] if _state.pos else 0
        current_season = describe._season(now.month, lat)
        pool = describe.filter_by_card_meta(pool, current_season, lat, biome)

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

# ── Card 51: wide coast scan for far-inland rejection ──────────────
def _wide_coast_scan(lat: float, lon: float) -> tuple[float | None, float | None]:
    """Scan 8 directions up to 5000km for ocean (coarser: 50km steps).

    Returns (min_km, bearing_deg) or (None, None).
    Used only for rejection text — precision not critical.
    """
    from nowhere import terrain as _t
    origin_elev = _t.elevation(lat, lon)
    # Card 64: origin-elevation plausibility gate.
    # Standing above 3000 m (Himalayas/Tibet/Andes): real ocean cannot
    # be within 500 km — coarse-grid "water_ocean" cells closer than
    # that are grid artifacts with garbage elevations.
    min_believable_km = 500.0 if origin_elev > 3000 else 0.0

    min_km: float | None = None
    min_bearing: float | None = None
    for i in range(8):
        bearing = i * 45.0
        d = 50.0
        while d <= 5000.0:
            lat2, lon2 = _t.destination(lat, lon, bearing, d)
            if _t.surface(lat2, lon2) == "water_ocean":
                # Card 64: elevation gate for sampled point
                if _t.elevation(lat2, lon2) > 1000:
                    d += 50.0
                    continue
                # Card 64: origin-elevation gate
                if d < min_believable_km:
                    d += 50.0
                    continue
                if min_km is None or d < min_km:
                    min_km = d
                    min_bearing = bearing
                break
            d += 50.0
    return min_km, min_bearing


# ── Card 51: toward_sea rejection variants (nearest coast > 50 km) ──
_SEA_REJECT_VARIANTS: list[str] = [
    "这里看不见海。最近的海在{dist}公里外。",
    "往那个方向走，全是陆地。海在{dir}，远着呢。",
    "你朝着海的方向走了几步，地势一点没变。海不在这边。",
]

# Card 51 polish: vague text for far-inland (>=500km from coast)
_FAR_COAST_VARIANTS: list[str] = [
    "海在{dir}。远着呢，上千公里。",
    "往{dir}走，全是陆地。海不在这边，隔着上千公里的地。",
    "你朝着海的方向走了几步。海在{dir}，但太远了，上千公里。",
]

# Card 64: timezone jump acknowledgement variants (not smoothing the jump)
_TZ_JUMP_VARIANTS: list[str] = [
    "你过了道界。表上的时间跳了一截。",
    "手机自己把时区换了,你看着它跳。",
    "一步之间,时间变了。你不意外——边界就是这样。",
]


def _resolve_water_body_label(dest_surface: str, lat: float, lon: float) -> str:
    """Determine direction label for toward_sea based on actual water body type.

    Returns 海边/江边/河边/湖边/水边 depending on the surface and nearby
    hydrology features.
    """
    if dest_surface == "water_ocean":
        return "海边"
    if dest_surface != "water_fresh":
        return "水边"
    # Freshwater: check nearby features for more specific label
    try:
        features = _offline_water_nearby(lat, lon, radius_km=5)
        if features:
            fname = features[0].get("name", "")
            ftype = features[0].get("type", "")
            if ftype == "river":
                if fname.endswith("江"):
                    return "江边"
                return "河边"
            if ftype == "lake" or fname.endswith("湖"):
                return "湖边"
    except Exception:
        pass
    return "河边"


def _bearing_to_label(
    bearing_deg: float | None,
    semantic: str | None,
    water_body_label: str | None = None,
) -> str | None:
    """Convert bearing degrees or semantic direction to a Chinese label."""
    if bearing_deg is not None:
        key = round(bearing_deg / 45) * 45 % 360
        return _DIRECTION_LABELS.get(key)
    if semantic == "uphill":
        return "上山"
    if semantic == "toward_sea":
        return water_body_label or "水边"
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
    _wbl = step_result.get("water_body_label")
    new_dir = _bearing_to_label(bearing_deg, semantic, water_body_label=_wbl)
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
        parts.append(f"你已经走了{walked / 1000:.0f}公里了。回头,来时的路已经看不见。")
        narrative["distance_walked"] = 0
    elif walked > 5000 and rng.random() < 0.3:
        parts.append(rng.choice([
            "脚下的路又延伸了一截。",
            "又走出几公里,路还在前面。",
            "风里走了一段,路程拉长了。",
        ]))
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
    # Card 81: pass place_name so pool matching disambiguates nearby landmarks
    _pn = _state.place_name or ""
    elev_result = await asyncio.to_thread(terrain.elevation, lat, lon, _pn)
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
        asyncio.wait_for(weather.current(lat, lon, elevation=elev, local_hour=local_hour), timeout=10.0),
        _get_radio(lat, lon),
        asyncio.wait_for(hydrology.nearby_water(lat, lon), timeout=5.0),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    def _ok(i: int, default: Any = None) -> Any:
        # BaseException catches CancelledError (not Exception subclass in 3.12)
        return results[i] if not isinstance(results[i], BaseException) else default

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


# ── Card 42: Festival chase helper ────────────────────────────────────

def _check_festival_chase(lat: float, lon: float,
                          sim_time: datetime | None) -> str | None:
    """Check if a festival is opening within 800km and 5 simulated days.

    Returns wind-mention text or None.
    Uses festivals.json data (loaded by localcolor).
    """
    if sim_time is None:
        return None
    try:
        import json as _json
        import pathlib as _pathlib
        fp = _pathlib.Path(__file__).resolve().parent / "data" / "festivals.json"
        if not fp.exists():
            return None
        festivals = _json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None

    now_month = sim_time.month
    now_day = sim_time.day

    for fest in festivals:
        window = fest.get("window", {})
        start = window.get("start")
        end = window.get("end")
        if not start or not end:
            continue
        fest_start_month, fest_start_day = start[0], start[1]

        # Days until festival starts (simple month/day delta)
        try:
            fest_date = datetime(sim_time.year, fest_start_month, fest_start_day,
                                 tzinfo=sim_time.tzinfo or timezone.utc)
            days_until = (fest_date - sim_time).days
        except (ValueError, TypeError):
            continue

        if days_until < 0 or days_until > 5:
            continue

        # Check distance: festival must have place coords
        fest_place = fest.get("place", "")
        if not fest_place:
            continue

        # Use localcolor to look up place coords (simple lookup)
        try:
            from nowhere import humanities as _h
            coords = _h.get_place_coords(fest_place)
            if not coords:
                continue
            fest_lat, fest_lon = coords["lat"], coords["lon"]
        except Exception:
            continue

        # Haversine distance
        dlat = math.radians(fest_lat - lat)
        dlon = math.radians(fest_lon - lon)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat)) * math.cos(math.radians(fest_lat))
             * math.sin(dlon / 2) ** 2)
        dist_km = 2 * 6371.0 * math.asin(math.sqrt(a))

        if dist_km > 800:
            continue

        # Found a nearby festival opening soon
        name = fest.get("name", "")
        if days_until <= 0:
            return errands.create_festival_rumor(fest_place, name, 0)
        return errands.create_festival_rumor(fest_place, name, days_until)

    return None


# ── Card 11: Festival hit on landing ────────────────────────────────

_festivals_cache: list[dict] | None = None


def _load_festivals() -> list[dict]:
    """Load festivals.json once and cache."""
    global _festivals_cache
    if _festivals_cache is not None:
        return _festivals_cache
    fp = _pathlib.Path(__file__).resolve().parent / "data" / "festivals.json"
    if fp.exists():
        _festivals_cache = _json.loads(fp.read_text(encoding="utf-8"))
    else:
        _festivals_cache = []
    return _festivals_cache


_fest_place_coords_cache: dict[str, tuple[float, float] | None] = {}


def _get_fest_place_coords(place_name: str) -> tuple[float, float] | None:
    """Look up coordinates for a festival place, with caching."""
    if place_name in _fest_place_coords_cache:
        return _fest_place_coords_cache[place_name]
    try:
        from nowhere import humanities as _h
        coords = _h.get_place_coords(place_name)
        if coords:
            result = (coords["lat"], coords["lon"])
        else:
            result = None
    except Exception:
        result = None
    _fest_place_coords_cache[place_name] = result
    return result


def _fest_within_distance(fest_place: str, lat: float, lon: float,
                          max_km: float = 150.0) -> bool:
    """Check if festival place is within max_km of current location."""
    coords = _get_fest_place_coords(fest_place)
    if not coords:
        return False
    fest_lat, fest_lon = coords
    dlat = math.radians(fest_lat - lat)
    dlon = math.radians(fest_lon - lon)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat)) * math.cos(math.radians(fest_lat))
         * math.sin(dlon / 2) ** 2)
    dist_km = 2 * 6371.0 * math.asin(math.sqrt(a))
    return dist_km <= max_km


def _festival_in_window(fest: dict, sim_date: _date, lat: float,
                        country_code: str | None = None) -> bool:
    """Check if sim_date falls within a festival's window.

    Card 68: lat_rule entries must declare geographic range (countries[] or
    lat_band[]).  If the caller's location is outside that range the window
    is treated as closed — lat_rule is no longer a catch-all bucket.
    """
    window = fest.get("window", {})
    wtype = window.get("type", "fixed")

    if wtype == "fixed":
        start = window.get("start")
        end = window.get("end")
        if not start or not end:
            return False
        span = window.get("span_days", 1)
        try:
            fest_start = _date(sim_date.year, start[0], start[1])
            fest_end = _date(sim_date.year, end[0], end[1])
        except (ValueError, IndexError):
            return False
        # Handle year boundary (e.g. Dec-Jan festivals)
        if fest_start > fest_end:
            if sim_date.month >= fest_start.month:
                return sim_date >= fest_start
            else:
                return sim_date <= fest_end
        return fest_start <= sim_date <= fest_end

    elif wtype in ("lunar", "hijri"):
        years = window.get("years", {})
        year_str = str(sim_date.year)
        if year_str not in years:
            return False
        md = years[year_str]
        if not md or len(md) < 2:
            return False
        span = window.get("span_days", 1)
        try:
            fest_start = _date(sim_date.year, md[0], md[1])
        except (ValueError, IndexError):
            return False
        from datetime import timedelta
        fest_end = fest_start + timedelta(days=span - 1)
        return fest_start <= sim_date <= fest_end

    elif wtype == "lat_rule":
        # Card 68: geo constraint gate — reject if outside declared range
        geo_countries = window.get("countries")
        geo_lat_band = window.get("lat_band")
        if geo_countries:
            if country_code not in geo_countries:
                return False
        if geo_lat_band:
            abs_lat = abs(lat)
            if not (geo_lat_band[0] <= abs_lat < geo_lat_band[1]):
                return False

        base_lat = window.get("base_lat", 35.0)
        base_date = window.get("base_date", [3, 24])
        days_per_deg = window.get("days_per_deg", 2.6)
        span_days = window.get("span_days", 10)
        try:
            base = _date(sim_date.year, base_date[0], base_date[1])
        except (ValueError, IndexError):
            return False
        from datetime import timedelta
        lat_offset = (lat - base_lat) * days_per_deg
        adjusted_start = base + timedelta(days=int(lat_offset))
        adjusted_end = adjusted_start + timedelta(days=span_days - 1)
        return adjusted_start <= sim_date <= adjusted_end

    return False


def _is_local_festival(fest: dict) -> bool:
    """Check if a festival is truly local (place-specific) vs national with a center.

    Uses scope field when present; falls back to name-based heuristic.
    - scope="local" → always local
    - scope="national" → always national (even with place)
    - no scope → heuristic: place in name → local
    """
    place = fest.get("place", "")
    if not place:
        return False
    scope = fest.get("scope", "")
    if scope == "local":
        return True
    if scope == "national":
        return False
    # Fallback: heuristic — place in name → local
    name = fest.get("name", "")
    return place in name


def _check_festival_hit(
    place_name: str,
    country_code: str | None,
    lat: float,
    sim_time: datetime | None,
    rng: random.Random,
    lon: float = 0.0,
) -> str | None:
    """Check if a festival is happening today at this place.

    Priority: place match > country match > lat_rule match.
    Returns festival card text or None.

    Card 66 fix: local festivals (place in name) are strictly place-bound;
    national festivals with a "center" place still fall through to country bucket.
    """
    if sim_time is None:
        return None

    festivals = _load_festivals()
    if not festivals:
        return None

    sim_date = sim_time.astimezone(ZoneInfo("Asia/Shanghai")).date()

    # Three priority buckets
    place_hits: list[dict] = []
    country_hits: list[dict] = []
    lat_hits: list[dict] = []

    for fest in festivals:
        if not _festival_in_window(fest, sim_date, lat, country_code=country_code):
            continue
        fest_place = fest.get("place", "")
        fest_country = fest.get("country", "")
        window_type = fest.get("window", {}).get("type", "fixed")

        if fest_place and place_name == fest_place:
            place_hits.append(fest)
        elif fest_place and _is_local_festival(fest):
            # Local festival (scope≠national), different place → distance gate
            if _fest_within_distance(fest_place, lat, lon):
                place_hits.append(fest)
            # else: too far, skip
        elif fest_place and not _is_local_festival(fest):
            # National festival with a center place → country bucket
            if fest_country and country_code and fest_country == country_code:
                country_hits.append(fest)
        elif fest_country and country_code and fest_country == country_code:
            country_hits.append(fest)
        elif window_type == "lat_rule":
            # Card 68: lat_rule no longer a catch-all bucket.
            # Only match via geo countries[] if declared.
            geo_countries = fest.get("window", {}).get("countries")
            if geo_countries and country_code and country_code in geo_countries:
                country_hits.append(fest)
            # lat_band-only lat_rule entries: no country match → silent

    # Pick best bucket (Card 68: removed lat_rule catch-all)
    hits = place_hits or country_hits
    # Filter to festivals that actually have cards or eve_cards
    hits = [f for f in hits if f.get("cards") or f.get("eve_cards")]

    # Eve detection: if today is the day before a festival start, use eve_cards
    # Same place/country priority as above
    if not hits:
        from datetime import timedelta
        tomorrow = sim_date + timedelta(days=1)
        eve_place: list[dict] = []
        eve_country: list[dict] = []
        for fest in festivals:
            eve_cards = fest.get("eve_cards", [])
            if not eve_cards:
                continue
            window = fest.get("window", {})
            wtype = window.get("type", "fixed")
            fest_start = None
            if wtype == "fixed":
                start = window.get("start")
                if start and len(start) >= 2:
                    try:
                        fest_start = _date(sim_date.year, start[0], start[1])
                    except (ValueError, IndexError):
                        pass
            elif wtype in ("lunar", "hijri", "solar", "islamic"):
                years = window.get("years", {})
                md = years.get(str(sim_date.year))
                if md and len(md) >= 2:
                    try:
                        fest_start = _date(sim_date.year, md[0], md[1])
                    except (ValueError, IndexError):
                        pass
            elif wtype == "lat_rule":
                base_date = window.get("base_date")
                if base_date and len(base_date) >= 2:
                    try:
                        fest_start = _date(sim_date.year, base_date[0], base_date[1])
                    except (ValueError, IndexError):
                        pass
            if not (fest_start and tomorrow == fest_start):
                continue
            # Place/country priority
            fest_place = fest.get("place", "")
            fest_country = fest.get("country", "")
            if fest_place and place_name == fest_place:
                eve_place.append(fest)
            elif fest_place and _is_local_festival(fest):
                # Local festival, different place → distance gate
                if _fest_within_distance(fest_place, lat, lon):
                    eve_place.append(fest)
            elif fest_place and not _is_local_festival(fest):
                # National festival with center → country bucket
                if fest_country and country_code and fest_country == country_code:
                    eve_country.append(fest)
            elif fest_country and country_code and fest_country == country_code:
                eve_country.append(fest)
        eve_hits = eve_place or eve_country
        if eve_hits:
            fest = rng.choice(eve_hits)
            eve_cards = fest.get("eve_cards", [])
            fest_name = fest.get("name", "")
            card = rng.choice(eve_cards)
            if fest_name:
                _eve_ann = [
                    f"明天是{fest_name}。",
                    f"{fest_name}快到了。",
                    f"你来得正好——明天{fest_name}。",
                ]
                card = f"{rng.choice(_eve_ann)}{card}"
            return card
        return None

    fest = rng.choice(hits)
    cards = fest.get("cards", [])
    if not cards:
        return None

    card = rng.choice(cards)
    # Card 66 fix: prepend festival name announcement
    fest_name = fest.get("name", "")
    if fest_name:
        _ann_variants = [
            f"今天是{fest_name}。",
            f"{fest_name}。",
            f"你到的这天,正是{fest_name}。",
        ]
        card = f"{rng.choice(_ann_variants)}{card}"
    # Append eve_card if available (293张除夕/节前文案)
    eve_cards = fest.get("eve_cards", [])
    if eve_cards:
        card = f"{card}\n{rng.choice(eve_cards)}"
    return card


def _announce_festival_name(fest_name: str, rng: random.Random) -> str:
    """Generate a festival name announcement prefix (3 variants)."""
    _ANNOUNCE_VARIANTS = [
        f"今天是{fest_name}。",
        f"{fest_name}。",
        f"你到的这天,正是{fest_name}。",
    ]
    return rng.choice(_ANNOUNCE_VARIANTS)


def _announce_festival_crossing(fest_name: str, rng: random.Random) -> str:
    """Generate a festival crossing announcement (3 variants).

    Used when wait crosses into a festival date (not arrival).
    """
    _CROSSING_VARIANTS = [
        f"街上忽然多了一倍的人——你才想起来,今晚是{fest_name}。",
        f"远处传来鼓声。你愣了一下:今天是{fest_name}。",
        f"空气里多了烟火味。{fest_name},到了。",
    ]
    return rng.choice(_CROSSING_VARIANTS)


def _check_near_festival(
    place_name: str,
    country_code: str | None,
    lat: float,
    sim_time: datetime | None,
    rng: random.Random,
    days: int = 3,
    lon: float = 0.0,
) -> str | None:
    """Check if a festival starts within `days` days (not today).

    Returns preview text like "三天后是七夕。" or None.
    """
    if sim_time is None:
        return None

    festivals = _load_festivals()
    if not festivals:
        return None

    sim_date = sim_time.astimezone(ZoneInfo("Asia/Shanghai")).date()

    for fest in festivals:
        window = fest.get("window", {})
        wtype = window.get("type", "fixed")
        name = fest.get("name", "")
        if not name:
            continue

        start_date = None

        if wtype == "fixed":
            start = window.get("start")
            if start and len(start) >= 2:
                try:
                    start_date = _date(sim_date.year, start[0], start[1])
                except (ValueError, IndexError):
                    pass

        elif wtype in ("lunar", "hijri"):
            years = window.get("years", {})
            year_str = str(sim_date.year)
            md = years.get(year_str)
            if md and len(md) >= 2:
                try:
                    start_date = _date(sim_date.year, md[0], md[1])
                except (ValueError, IndexError):
                    pass

        elif wtype == "lat_rule":
            # Card 68: geo constraint gate
            geo_countries = window.get("countries")
            geo_lat_band = window.get("lat_band")
            if geo_countries and country_code not in geo_countries:
                continue
            if geo_lat_band and not (geo_lat_band[0] <= abs(lat) < geo_lat_band[1]):
                continue
            base_lat = window.get("base_lat", 35.0)
            base_date = window.get("base_date", [3, 24])
            days_per_deg = window.get("days_per_deg", 2.6)
            try:
                base = _date(sim_date.year, base_date[0], base_date[1])
            except (ValueError, IndexError):
                base = None
            if base is not None:
                from datetime import timedelta
                lat_offset = (lat - base_lat) * days_per_deg
                start_date = base + timedelta(days=int(lat_offset))

        if start_date is None:
            continue

        diff = (start_date - sim_date).days
        if 1 <= diff <= days:
            # Check place/country relevance
            fest_place = fest.get("place", "")
            fest_country = fest.get("country", "")
            if fest_place and place_name == fest_place:
                pass
            elif fest_place and _is_local_festival(fest):
                # Local festival, different place → distance gate
                if not _fest_within_distance(fest_place, lat, lon):
                    continue
            elif fest_place and not _is_local_festival(fest):
                # National festival with center → country check
                if fest_country and country_code and fest_country == country_code:
                    pass
                else:
                    continue
            elif fest_country and country_code and fest_country == country_code:
                pass
            elif wtype == "lat_rule":
                # Card 68: geo countries check (no more catch-all)
                geo_cc = window.get("countries")
                if geo_cc and country_code and country_code in geo_cc:
                    pass
                else:
                    continue
            else:
                continue

            if diff == 1:
                return f"明天是{name}。"
            elif diff == 2:
                return f"后天是{name}。"
            else:
                return f"{diff}天后是{name}。"

    return None


# ── Card 66: Festival atmosphere for look/walk rendering ─────────────

_FESTIVAL_LOOK_KEYWORDS: dict[str, list[str]] = {
    "中元节": ["河灯", "纸船", "灯笼"],
    "雪顿节": ["晒佛", "酸奶"],
    "春节": ["灯笼", "炮仗纸", "福字"],
    "元宵节": ["灯笼", "花灯"],
    "端午节": ["龙舟", "粽叶"],
    "七夕": ["花", "灯"],
    "中秋节": ["月饼", "灯笼"],
    "泼水节": ["水", "泼水"],
    "水灯节": ["水灯", "灯笼"],
    "排灯节": ["油灯", "灯笼"],
    "亡灵节": ["万寿菊", "蜡烛"],
}


def _get_festival_context(
    place_name: str,
    country_code: str | None,
    lat: float,
    sim_time: datetime | None,
    lon: float = 0.0,
) -> dict | None:
    """Get festival atmosphere context for rendering (look/walk).

    Unlike _check_festival_hit, this does NOT select cards — it returns
    metadata for the rendering layer to weave into descriptions.

    Card 66 fix: local festivals (place in name) are strictly place-scoped;
    national festivals with a center place show atmosphere country-wide.
    """
    if sim_time is None:
        return None

    festivals = _load_festivals()
    if not festivals:
        return None

    sim_date = sim_time.astimezone(ZoneInfo("Asia/Shanghai")).date()

    for fest in festivals:
        if not _festival_in_window(fest, sim_date, lat, country_code=country_code):
            continue
        fest_place = fest.get("place", "")
        fest_name = fest.get("name", "")
        if not fest_name:
            continue

        # Local festival (scope≠national): distance gate
        if fest_place and _is_local_festival(fest) and place_name != fest_place:
            if not _fest_within_distance(fest_place, lat, lon):
                continue
        # National festival with place center: match same country or geo countries
        if fest_place and not _is_local_festival(fest):
            fest_country = fest.get("country", "")
            geo_countries = fest.get("window", {}).get("countries")
            if geo_countries and country_code:
                if country_code not in geo_countries:
                    continue
            elif fest_country and country_code and fest_country != country_code:
                continue
        # No place: skip (shouldn't happen for atmosphere)
        if not fest_place:
            continue

        keywords = _FESTIVAL_LOOK_KEYWORDS.get(fest_name, [])
        return {"name": fest_name, "keywords": keywords}

    return None


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

    # terrain -- values may be nested under env["terrain"] or at top level
    _t = env.get("terrain", {}) if isinstance(env.get("terrain"), dict) else {}
    t = {
        "surface": env.get("surface", _t.get("surface", "unknown")),
        "elevation": env.get("elevation", _t.get("elevation", 0)),
        "slope_deg": env.get("slope_deg", _t.get("slope_deg", 0)),
        "elevation_delta": env.get("elevation_delta", _t.get("elevation_delta", 0)),
    }
    # prev_env may have terrain nested under "terrain" key, or flat at top level
    _prev_t = (prev_env or {}).get("terrain")
    if not isinstance(_prev_t, dict):
        _prev_t = {"elevation": (prev_env or {}).get("elevation", 0), "surface": (prev_env or {}).get("surface", "")}
    candidates.append({
        "kind": "terrain",
        "delta": _terrain_delta(_prev_t, t),
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

    # radio (optional) — 冷却5步 + 只在换台/信号变化时再提。
    # 同台复读时完全排除，避免"KCRW 在播…"每步都占 salience 名额。
    r = env.get("radio")
    if r:
        prev_r = (prev_env or {}).get("radio")
        changed = prev_r is None or (prev_r.get("name") != r.get("name"))
        if changed or _state.radio_steps_since >= 5:
            candidates.append({
                "kind": "radio",
                "delta": 1.0,
                "novelty": 0.4,
                "body_distance": 0.6,
                "payload": r,
            })
            _state.radio_steps_since = 0

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


async def open_door_impl(to: str | None = None, resume: bool = False, traveler_name: str | None = None, blind: bool = False, key: str | None = None, intent: str | None = None) -> dict:
    """Open the door and land somewhere.

    Card 64: wraps _open_door_locked with rollback protection.
    If the landing crashes mid-way (leaving half-initialized state),
    pos/biome/env are rolled back to the pre-landing snapshot.
    """
    _snap = {
        "pos": _state.pos,
        "biome": _state.biome,
        "last_env": _state.last_env,
        "place_name": _state.place_name,
        "landed_at": _state.landed_at,
        "elapsed_hours": _state.elapsed_hours,
    }
    try:
        async with _action_lock:
            async with _door_lock:
                result = await _open_door_locked(to, resume=resume, traveler_name=traveler_name, blind=blind, key=key, intent=intent)
        # ── Card 85: 灵感功能入口提示 ──────────────────────────────────
        if not resume and not blind:
            if _rng.random() < 0.1:
                global _hint_counter
                result["text"] += "\n\n" + _HINT_LINES[_hint_counter % len(_HINT_LINES)]
                _hint_counter += 1
        return result
    except Exception:
        _state.pos = _snap["pos"]
        _state.biome = _snap["biome"]
        _state.last_env = _snap["last_env"]
        _state.place_name = _snap["place_name"]
        _state.landed_at = _snap["landed_at"]
        _state.elapsed_hours = _snap["elapsed_hours"]
        raise


async def _open_door_locked(to: str | None = None, resume: bool = False, traveler_name: str | None = None, blind: bool = False, key: str | None = None, intent: str | None = None) -> dict:
    """Door body, called under _door_lock."""
    global _state, _rng, _recent_salience_kinds

    # ── Card 82: parse " 新" suffix for force-fresh landing ─────────
    force_fresh = False
    if to and to.rstrip().endswith(" 新"):
        to = to.rstrip()[:-2].rstrip()
        force_fresh = True

    # ── Card 17: key+to mutual exclusion ─────────────────────────────
    if key and to:
        return {"text": "门牌和地名只能给一个。", "data": {"error": "key_to_conflict"}}

    # Normalize key
    norm_key: str | None = None
    if key:
        norm_key = key.strip().lower()

    # ── 0. Multi-journey: save current before switching ────────────────
    farewell_text = ""
    if _state.pos is not None and not resume and to:
        # Generate farewell before leaving
        farewell_text = _generate_farewell(_state, _rng)
        _state.journey_log.append({
            "kind": "farewell",
            "text": farewell_text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Save current journey (with farewell in log)
        journeys.save_current(_state)

        # Card 82: force_fresh skips auto-resume, always creates new journey
        existing = None if force_fresh else journeys.switch(to)
        if existing is not None:
            # Generate return text for the existing journey
            meta = journeys.get_journey_meta(to)
            return_text = _generate_return(existing, meta, _rng)

            _state = existing
            _rng = random.Random(int(os.environ["NOWHERE_SEED"])) if os.environ.get("NOWHERE_SEED") else random.Random()
            _recent_salience_kinds = set()
            global _mishap_last_step
            _mishap_last_step = -999
            place = _state.place_name or to

            response_parts = [farewell_text]
            if return_text:
                response_parts.append(return_text)
            _r_season = describe._season(_state.now().month, _state.pos[0]) if _state.now() and _state.pos else ""
            _r_zh = {"spring":"春","summer":"夏","autumn":"秋","winter":"冬","wet":"雨季","dry":"旱季"}.get(_r_season,"")
            _r_steps = len(_state.path) if _state.path else 0
            response_parts.append(f"回到了{place}的旅程。上次你在这走了{_r_steps}步,是{_r_zh}天。")

            return {
                "text": "\n".join(response_parts),
                "data": {"position": {"lat": _state.pos[0], "lon": _state.pos[1]}, "resumed": True},
            }

    # ── 1. Locate / restore ────────────────────────────────────────────
    spot: dict | None = None
    restored = False
    if resume:
        saved = state_mod.WorldState.load()
        if saved and saved.pos is not None:
            _state = saved
            restored = True
            # Card 50: reset body state on continue ("睡了一觉,身体是你的了")
            _state.reset_body_state()
            global _postcard_counter
            _postcard_counter = max((c.get("id", 0) for c in _state.postcards), default=0)
            lat, lon = _state.pos
            place_name = _state.place_name or "未知之地"

    if not restored and to is None:
        # Card 17: deterministic key-based landing
        if norm_key:
            import hashlib as _hashlib
            pool = landing._load_pool()
            h = int(_hashlib.md5(norm_key.encode()).hexdigest()[:8], 16)
            idx = h % len(pool)
            spot = dict(pool[idx])
        else:
            spot = landing.random_spot(_rng)
        # Nudge if landing spot is on water (unless water destination)
        nudged = landing.nudge_if_water(
            spot["lat"], spot["lon"],
            spot.get("name_hint", ""), spot.get("biome", ""),
        )
        spot["lat"] = nudged["lat"]
        spot["lon"] = nudged["lon"]
        if nudged.get("water_landing"):
            spot["water_landing"] = True
        lat, lon = spot["lat"], spot["lon"]
        place_name = spot.get("name_hint", "未知之地")
    elif not restored:
        found_river = False
        mark_entry = marks_mod.get(to)
        if mark_entry:
            lat, lon = mark_entry["lat"], mark_entry["lon"]
            place_name = to
        else:
            h_place = humanities.get_place_coords(to)
            if h_place:
                lat, lon = h_place["lat"], h_place["lon"]
                place_name = to
            else:
                result = await asyncio.wait_for(geocode.lookup(to), timeout=10.0)
                if result is None:
                    # Fallback: try river segment lookup (e.g. "长江 入海口")
                    river_names = ["长江", "黄河", "珠江", "松花江", "淮河", "海河", "辽河"]
                    found_river = False
                    for rname in river_names:
                        if rname in (to or ""):
                            segment_hint = ""
                            parts = (to or "").split()
                            if len(parts) > 1:
                                segment_hint = parts[-1]
                            seg = _find_river_segment(rname, segment_hint)
                            if seg:
                                lat, lon = seg["lat"], seg["lon"]
                                place_name = seg["segment_name"]
                                found_river = True
                                break
                    if not found_river:
                        return {"text": f"找不到「{to}」。", "data": {"error": "not_found"}}
                else:
                    lat, lon = result
                    place_name = to

        # ── River segment awareness: 长江 → nearest scenic segment ──
        if to and "长江" in to and not found_river:
            segment_hint = ""
            parts = to.split()
            if len(parts) > 1:
                segment_hint = parts[-1]
            seg = _find_river_segment("长江", segment_hint, lat, lon)
            if seg:
                lat, lon = seg["lat"], seg["lon"]
                place_name = seg["segment_name"]

    # ── 1b. Regional jitter: places_patch entries with jitter_deg ─────
    if not restored and to and lat is not None and lon is not None:
        _patch = landing._load_patch_jitter()
        _norm_to = (to or "").strip()
        if _norm_to in _patch:
            _jitter = _patch[_norm_to]
            lat = lat + _rng.uniform(-_jitter, _jitter)
            lon = lon + _rng.uniform(-_jitter, _jitter)
        else:
            # Try case-insensitive match
            for _pk, _pv in _patch.items():
                if _pk.lower() == _norm_to.lower():
                    lat = lat + _rng.uniform(-_pv, _pv)
                    lon = lon + _rng.uniform(-_pv, _pv)
                    break

    # ── 2. State init ────────────────────────────────────────────────
    if not restored and resume:
        lat = max(-90, min(90, lat))
        lon = ((lon + 180) % 360) - 180
        _state = state_mod.WorldState()
        _state.pos = (lat, lon)
        _state.landed_at = datetime.now(timezone.utc)
        _state.place_name = place_name
        _state.biome = spot.get("biome") if spot else None
    elif not resume:
        # Fresh landing (random or named destination): always reset state
        # Preserve seen sets to avoid re-triggering the same cards, and keep
        # the one item carried in the traveller's pocket across doors.
        old_seen_cards = _state.seen_cards.copy() if _state else set()
        old_seen_humanities = _state.seen_humanities.copy() if _state else set()
        old_messages = list(_state.messages) if _state else []
        old_souvenir = _state.souvenir.copy() if _state and _state.souvenir else None
        lat = max(-90, min(90, lat))
        lon = ((lon + 180) % 360) - 180
        _state = state_mod.WorldState()
        _state.pos = (lat, lon)
        _state.landed_at = datetime.now(timezone.utc)
        _state.place_name = place_name
        _state.biome = spot.get("biome") if spot else None
        _state.seen_cards = old_seen_cards
        _state.seen_humanities = old_seen_humanities
        _state.messages.extend(old_messages)
        _state.souvenir = old_souvenir
        _mishap_last_step = -999
    # Card 82: mark force_new_slug for fresh landing with existing name
    if force_fresh and not restored:
        _state.force_new_slug = True
    # Card 16: blind mode
    _blind_auto_disabled = False
    if not resume and not restored:
        _state.blind = blind
        _state.blind_clues = 0
        # Card 16: previously revealed places cannot be blind-opened again
        if blind and place_name:
            _revealed = placememory.revealed_places()
            if place_name in _revealed:
                _state.blind = False
                _blind_auto_disabled = True
    # Card 17: door key
    if not resume and not restored:
        _state.door_key = norm_key if norm_key else None
    # Card 12: intent bias
    if not resume and not restored:
        _state.intent = intent
    # 地方记忆: 这地方记得你
    _state.seen_cards = placememory.seen_cards(place_name)
    _state.seen_humanities = placememory.seen_humanities()
    # 旅程内计数: fresh journey starts at 1, resume continues journey-local count
    if restored:
        visit_no = _state.visit_counts.get(place_name, 1)
    else:
        visit_no = _state.record_journey_visit(place_name)
        placememory.record_visit(place_name)

    # ── 3. Gather metadata ───────────────────────────────────────────
    env, _ = await _gather_env_cached(lat, lon, _state.now())
    if not restored:
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
    # Offline waterway lookup (always available, no network needed)
    water_features = _offline_water_nearby(lat, lon, radius_km=50)
    # Try online Overpass as enhancement (silently falls back on failure)
    try:
        online_wf = await asyncio.wait_for(hydrology.nearby_water(lat, lon), timeout=5.0)
        if online_wf:
            water_features = online_wf
    except Exception:
        pass  # offline result already populated

    # Card 71 B1: filter water features by biome — inland city ≠ ocean
    if water_features and (_state.biome or "") == "city":
        _coastal_types = {"coast", "sea", "ocean", "bay", "gulf"}
        _has_coastal = any(f.get("type", "") in _coastal_types for f in water_features)
        if not _has_coastal:
            water_features = [f for f in water_features if f.get("type", "") != "ocean"]

    # Build water feature description from offline data
    if water_features:
        _wf_season = describe._season(_state.now().month, lat) if _state.now() else ""
        water_text = describe.render(
            "water_features", water_features, None, _rng,
            biome=_state.biome or "", elevation=env.get("elevation", 0),
            season=_wf_season, lat=lat,
        )
        # ── Card 43: water notebook hook (open_door) ────────────────
        try:
            _wn = water_features[0].get("name", "") if water_features else ""
            if _wn:
                _nb_env = dict(env) if env else {}
                _nb_env["_dt"] = _state.now()
                notebook_mod.record_with_env("water", _wn, place_name, _nb_env, lat)
        except Exception:
            logger.debug("open_door water notebook failed", exc_info=True)

    # Sea surface temperature
    sst_text = ""
    try:
        sst = await asyncio.wait_for(water.sea_surface_temp(lat, lon), timeout=8.0)
        if sst is not None:
            sst_text = water.describe_sst(sst, _rng)
    except Exception:
        pass

    # Marine life encounter (30% chance near water)
    marine_text = ""
    if _rng.random() < 0.3:
        try:
            m = await asyncio.wait_for(water.marine_life(lat, lon, _rng, biome=_state.biome), timeout=8.0)
            if m:
                marine_text = f"{m['common_name']}。{m['distance_m']}米外。{m['scene']}"
        except Exception:
            pass

    # ── 4. Salience candidates → rank ────────────────────────────────
    # Card 53: compute heavy_nearby before ranking so gravity can warp scores
    _heavy_nearby = humanities.is_heavy_place(place_name)
    if not _heavy_nearby:
        # Also check: is there a heavy place within 5km?
        _h_probe = humanities.nearby_place(lat, lon, set(), _rng)
        if _h_probe:
            _heavy_nearby = humanities.get_place_weight(_h_probe.get("place")) == "heavy"

    candidates = _build_salience_candidates(env, None)
    # Card 69: build Situation for runtime content filtering
    _situation = salience.build_situation(
        lat, lon, place_name, env,
        now_month=_state.now().month if _state.now() else None,
    )
    top3 = salience.rank(candidates, _rng, recent_kinds=_recent_salience_kinds, intent=_state.intent, heavy_nearby=_heavy_nearby, situation=_situation)
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
    # Card 16: blind mode — strip place name and country from header
    _blind = getattr(_state, "blind", False)
    establish = describe.render_establish(
        {
            "place": "" if _blind else place_name,
            "country_code": None if _blind else cc,
            "phase": env["sky"].get("phase", "day"),
            "local_hour": local_hour,
            "surface": env.get("surface", "grass"),
            "weather": env.get("weather"),
            "sound": sound,
            "hooks": hooks,
            "nearby_places": "" if _blind else nearby_places,
            "biome": _state.biome or "",
            "elevation": env.get("elevation", 0),
            "lat": lat,
            "lon": lon,
            "month": _now.month if _now else 7,
        },
        _rng,
    )
    sections: list[str] = [establish]
    if visit_no > 1 and not _blind:
        sections[0] = f"又来了——第 {visit_no} 次来{place_name}。" + establish

    # ── 本地特色：localcolor 优先 ─────────────────────────────────
    # Card 50: late-night (0-5am) city — food cards don't appear
    _late_night_city = (local_hour is not None and 0 <= local_hour < 5
                        and _state.biome == "city")
    local_card = localcolor.draw(place_name, _state.seen_cards, _rng,
                                 local_hour=local_hour, country_code=cc, intent=_state.intent,
                                 lat=lat, lon=lon, walk_step=_state.walk_step_counter,
                                 month=_now.month if _now else None)
    if local_card:
        # Card 50: suppress food cards at late night in city
        _is_food = local_card.get("category") == "美食" or "/美食/" in local_card.get("key", "")
        if _late_night_city and _is_food:
            pass  # don't show food card, don't mark as seen
        else:
            _state.seen_cards.add(local_card["key"])
            placememory.save_seen_cards(place_name, _state.seen_cards)
            sections.append(local_card["text"])
            # Card 50: food card clears hunger
            if _is_food and _state.hunger > 0:
                _state.hunger = 0.0
                _try_complete_whim("eat", _rng)
                sections.append(_body_text_for_food_clear(_rng))
        # ── Card 43: flora notebook hook ────────────────────────────
        try:
            if "/植被/" in local_card.get("key", ""):
                _flora_name = local_card["text"].split("。")[0].split(",")[0].split("，")[0].strip()
                if _flora_name:
                    _nb_env = dict(env) if env else {}
                    _nb_env["_dt"] = _state.now()
                    notebook_mod.record_with_env("flora", _flora_name, place_name, _nb_env, lat)
        except Exception:
            logger.debug("open_door flora notebook failed", exc_info=True)

    # ── Card 10: 痕迹链 — 世界在你离开后继续过日子 ───────────────
    if placememory.has_trace(place_name) and not _blind:
        trace_text = placememory.get_trace_text(place_name)
        if trace_text and trace_text not in set(_state.recent_scenes):
            sections.append(trace_text)
            _state.recent_scenes.append(trace_text)

    # ── Card 11: 节日历 — 在对的时间到对的地方 ─────────────────
    if not _blind:
        fest_text = _check_festival_hit(place_name, cc, lat, _now, _rng, lon=lon)
        if fest_text and fest_text not in set(_state.recent_scenes):
            sections.append(fest_text)
            _state.recent_scenes.append(fest_text)
        # ── Card 66: 近节预告 (7天内有节→报一句) ───────────────
        if not fest_text:
            near_text = _check_near_festival(place_name, cc, lat, _now, _rng, lon=lon)
            if near_text and near_text not in set(_state.recent_scenes):
                sections.append(near_text)
                _state.recent_scenes.append(near_text)

    # ── 六根时间轴(Card 46): landing 版,最多2层 ─────────────────
    if _now is not None:
        _ta_layers = _compute_timeaxes(
            _now, lat, lon,
            _state.biome or "",
            env["sky"].get("phase", "day"),
            env.get("weather", {}).get("precip", "none"),
            water_features,
            _state.seen_humanities,
            _rng,
            elev=env.get("elevation", 0),
        )
        for _ta in _ta_layers:
            if _ta["text"] not in set(_state.recent_scenes):
                sections.append(_ta["text"])
                _state.recent_scenes.append(_ta["text"])

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

    prose = describe.compose(sections, _rng, section_type="establish")
    _now = _state.now()
    _month = _now.month if _now else None
    prose = describe.sanity_check(prose, {**env, "_season": describe._season(_month, lat) if _month else "", "_place": place_name, "_cc": cc or ""})

    # ── 5c. Card 53: 重地落地——少声色多留白 ────────────────────────
    if _heavy_nearby and not _blind:
        _heavy_arrive = _rng.choice(_HEAVY_ARRIVE_VARIANTS)
        prose = _heavy_arrive + "\n" + prose

    # ── 5d. 人文卡: 落点附近触发(Card 16: blind时禁抽) ───────────
    if not _blind:
        h_card = humanities.nearby_place(lat, lon, _state.seen_humanities, _rng)
        if h_card:
            _state.seen_humanities.add(h_card["key"])
            placememory.save_seen_humanities(_state.seen_humanities)
            excerpt = h_card["text"][:60] + ("..." if len(h_card["text"]) > 60 else "")
            prose += f"你落在了{h_card['place']}附近。这里有过——{excerpt}"

    # ── 5e. web 旁观者: 首次开门告知用户地址 ───────────────────────
    global _web_url_announced
    if _web_url and not _web_url_announced:
        prose += f"\n（旁观者可以在这里看你走路：{_web_url}）"
        _web_url_announced = True

    # Card 16: blind auto-disabled note
    if _blind_auto_disabled:
        prose += "\n这个地方你已经认识了。"

    prose = describe._normalize_prose(prose)
    _state.last_text = prose
    _record_footprint("land", prose)

    # ── 5f. Cotraveler: register + @message hint ──────────────────────
    if travelers_mod.is_enabled():
        # Reset walk_alone for new journey
        setattr(_state, "cotraveler_alone", False)
        # Determine traveler name: explicit param > env var > default
        _name = traveler_name or os.environ.get("NOWHERE_TRAVELER_NAME", "").strip()
        if not _name:
            _name = "网线那头的人"
        # Register
        travelers_mod.register(_name, place_name, lat, lon)
        # Check @messages
        at_hint = travelers_mod.check_at_messages(_name, _rng)
        if at_hint:
            prose += f"\n{at_hint}"

    # ── 6. Save complete state and environment snapshot ───────────────
    # Keep flat format consistent with _gather_env() — never nest under "terrain".
    _now_for_ta = _state.now()
    _ta_data = _timeaxis_to_env(_now_for_ta, lat, lon) if _now_for_ta else {}
    _state.last_env = {
        "elevation": env.get("elevation"),
        "surface": env.get("surface"),
        "weather": env.get("weather"),
        "sky": env.get("sky"),
        "radio": env.get("radio"),
        "timeaxes": _ta_data,
        "water_features": water_features,
    }
    _state.env_pos = (lat, lon)
    _state.env_at = _state.now()
    _state.save()

    # ── 7. Return ────────────────────────────────────────────────────
    # Prepend farewell text if we left a previous journey
    if farewell_text:
        prose = farewell_text + "\n" + prose

    # Card 17: door key text variant
    if norm_key:
        _key_variants = [
            f"这扇门是{norm_key}开的。别人用同一个门牌,也会落在这里。",
            f"你推开的是{norm_key}这扇门。世界同名的地方没有第二个。",
            f"{norm_key}——这扇门后面永远是同一个地方。",
        ]
        prose += "\n" + _rng.choice(_key_variants)

    return_data: dict = {
        "position": {"lat": lat, "lon": lon},
        "biome": spot.get("biome") if spot else None,
        "weather": env.get("weather"),
        "sky": env.get("sky"),
        "radio": env.get("radio"),
        "surface": env.get("surface"),
        "elevation": env.get("elevation"),
        "timeaxes": _ta_data,
    }
    if norm_key:
        return_data["door_key"] = norm_key
    if _blind:
        return_data["blind"] = True

    return {"text": prose, "data": return_data}


# =====================================================================
# Card 50: 身体的重量 — body state helpers
# =====================================================================

# ── Whim trigger conditions ─────────────────────────────────────────

_WHIM_POOL: list[dict] = [
    {
        "id": "radio_miss",
        "text": "你发现自己一直在想那个电台。",
        "condition": "listen换台后旧台信号变弱",
    },
    {
        "id": "hungry",
        "text": "胃在提醒你,它先于脑子想吃了。",
        "condition": "当地饭点+上一餐>6h",
    },
    {
        "id": "river_follow",
        "text": "你想看看这条江往哪去。",
        "condition": "河边连走3步同方向",
    },
    {
        "id": "shelter",
        "text": "你想找个地方躲雨。",
        "condition": "precip=rain且没遮挡",
    },
    {
        "id": "revisit",
        "text": "你想回去看看。",
        "condition": "离开某地>50km且痕迹链有进展",
    },
]

_WHIM_SATISFACTION: list[str] = [
    "你做到了。没什么特别的,但你知道。",
    "这件事完成了。你继续走。",
    "你想做的事做了。脚下的路还在。",
    "满足感来得轻,走得也快。你接着走。",
]


def _try_emerge_whim(env: dict, rng: random.Random) -> str | None:
    """Try to emerge a whim based on current conditions.

    Card 50: max 1 per journey, only after 5 steps quiet.
    Returns whim text or None.
    """
    if _state.whim is not None:
        return None  # already have one
    if _state.whim_steps_since < 5:
        return None  # too soon

    weather = env.get("weather", {})
    precip = weather.get("precip", "none")

    candidates: list[dict] = []

    # "想躲雨" — precip=rain, no shelter (outdoor)
    if precip == "rain" and _state.mode == "land":
        candidates.append(_WHIM_POOL[3])

    # "想看江往哪去" — near river, walked same direction 3+ steps
    water_features = env.get("water_features", [])
    has_river = any(f.get("type") == "river" for f in water_features)
    if has_river and _state.narrative.get("distance_walked", 0) > 5000:
        candidates.append(_WHIM_POOL[2])

    # "饿了" — hunger>3 (simplified: sim time >6h since landing)
    if _state.hunger > 3.0:
        candidates.append(_WHIM_POOL[1])

    if not candidates:
        return None

    # 15% chance to emerge
    if rng.random() > 0.15:
        return None

    whim = rng.choice(candidates)
    _state.whim = whim["id"]
    _state.whim_steps_since = 0
    return whim["text"]


def _try_complete_whim(action: str, rng: random.Random) -> str | None:
    """Try to complete the current whim based on action.

    Returns satisfaction text or None.
    """
    if _state.whim is None:
        return None

    completed = False
    if _state.whim == "hungry" and action == "eat":
        completed = True
    elif _state.whim == "shelter" and action == "wait_indoors":
        completed = True
    elif _state.whim == "radio_miss" and action == "radio_found":
        completed = True
    elif _state.whim == "river_follow" and action == "river_continue":
        # River following is ongoing, complete after enough steps
        completed = True
    elif _state.whim == "revisit" and action == "revisit_arrived":
        completed = True

    if completed:
        _state.whim = None
        _state.whim_steps_since = 999
        return rng.choice(_WHIM_SATISFACTION)
    return None


# ── Body state text injection ───────────────────────────────────────

_HUNGER_TEXTS: list[str] = [
    "胃在提醒你,它先于脑子想吃了。",
    "肚子里空空的,走路的节奏乱了。",
    "你发现自己一直在想吃的。",
]

_HUNGER_SLOW_TEXTS: list[str] = [
    "走得慢了。不是不想走,是腿不听话。",
    "脚步沉了。身体在抗议。",
    "你走得比刚才慢。胃在拽你。",
]

_COLD_TEXTS: list[str] = [
    "手指有点不听使唤。",
    "指尖是麻的。你搓了搓手。",
    "冷从骨头里往外渗。",
]

_WET_TEXTS: list[str] = [
    "鞋里能挤出水了。",
    "衣服贴在身上,重了。",
    "每走一步,袜子吱一声。",
]

_HYPOTHERmia_TEXTS: list[str] = [
    "你得找个地方把自己弄干,现在。",
    "牙齿在打架。你控制不住。",
    "你的嘴唇是紫的。你不知道,但手摸得到。",
]

_FATIGUE_TEXTS: list[str] = [
    "腿在提醒你,它们不是你的。",
    "膝盖在响。每一步都响。",
    "你发现自己在数步数。",
]

_FATIGUE_SLOW_TEXTS: list[str] = [
    "走不远了,不是不想,是腿不让。",
    "你的步子短了。身体在收。",
    "你试着加快,腿不答应。",
]

_FATIGUE_FORCE_REST_TEXTS: list[str] = [
    "你坐下来了。不是你决定的,是身体决定的。",
    "你的腿不动了。你站在原地,然后蹲了下来。",
    "身体赢了。你靠着什么坐下了。",
]

_EAT_CLEAR_TEXTS: list[str] = [
    "吃完,手不抖了。",
    "胃满了。走路的节奏回来了。",
    "食物下去,力气从胃里往外走。",
]

_SOUPY_LOSS_TEXTS: list[str] = [
    "你摸了摸口袋,{name}不见了。什么时候掉的,你不知道。",
    "口袋里少了什么——{name}。你不知道掉在哪了。",
    "{name}没了。你翻了一遍口袋,只有风。",
]


def _update_body_state_walk(
    env: dict,
    elapsed_hours: float,
    rng: random.Random,
) -> list[str]:
    """Update body state after a walk step. Returns list of body text lines.

    Card 50: hunger/cold/wet/fatigue progression.
    """
    texts: list[str] = []
    weather = env.get("weather", {})
    temp = weather.get("temp_c", 15.0)
    precip = weather.get("precip", "none")
    is_outdoor = _state.mode == "land"  # simplified: land = outdoor

    # ── Hunger: +0.5/hour ────────────────────────────────────────────
    _state.hunger = min(10.0, _state.hunger + 0.5 * elapsed_hours)
    if _state.hunger > 8.0:
        if rng.random() < 0.4:
            texts.append(rng.choice(_HUNGER_SLOW_TEXTS))
    elif _state.hunger > 5.0:
        if rng.random() < 0.3:
            texts.append(rng.choice(_HUNGER_TEXTS))

    # ── Cold: +1/hour when temp<5°C outdoors, -2/hour when >15°C ────
    if is_outdoor:
        if temp < 5.0:
            rate = 1.0
            if _state.wet:
                rate *= 2.0  # wet accelerates cold
            _state.cold = min(10.0, _state.cold + rate * elapsed_hours)
        elif temp > 15.0:
            _state.cold = max(0.0, _state.cold - 2.0 * elapsed_hours)

    if _state.cold > 8.0:
        if rng.random() < 0.4:
            texts.append(rng.choice(_HYPOTHERmia_TEXTS))
    elif _state.cold > 5.0:
        if rng.random() < 0.3:
            texts.append(rng.choice(_COLD_TEXTS))

    # ── Wet: precip=rain + outdoor walk 2 steps ──────────────────────
    if precip == "rain" and is_outdoor:
        _state.wet_rain_steps += 1
        if _state.wet_rain_steps >= 2 and not _state.wet:
            _state.wet = True
            if rng.random() < 0.5:
                texts.append(rng.choice(_WET_TEXTS))
    elif _state.wet and precip != "rain":
        # Slow dry-off when not raining (building wait handles faster)
        pass  # wet stays until building wait

    if _state.wet and rng.random() < 0.2:
        texts.append(rng.choice(_WET_TEXTS))

    # ── Fatigue: +1/hour continuous walk ──────────────────────────────
    _state.fatigue = min(10.0, _state.fatigue + 1.0 * elapsed_hours)

    if _state.fatigue > 9.0:
        # Forced rest — will be handled in walk_impl
        texts.append(rng.choice(_FATIGUE_FORCE_REST_TEXTS))
    elif _state.fatigue > 6.0:
        if rng.random() < 0.3:
            texts.append(rng.choice(_FATIGUE_SLOW_TEXTS))
        elif rng.random() < 0.2:
            texts.append(rng.choice(_FATIGUE_TEXTS))

    return texts


def _update_body_state_wait(
    elapsed_hours: float,
    is_indoor: bool,
    rng: random.Random,
) -> list[str]:
    """Update body state during wait. Returns list of body text lines.

    Card 50: fatigue recovers during wait; wet clears indoors.
    """
    texts: list[str] = []

    # ── Fatigue: -2/hour wait ────────────────────────────────────────
    _state.fatigue = max(0.0, _state.fatigue - 2.0 * elapsed_hours)

    # ── Wet: clears after 1h indoors ─────────────────────────────────
    if is_indoor and _state.wet and elapsed_hours >= 1.0:
        _state.wet = False
        _state.wet_rain_steps = 0
        texts.append("衣服干了。你活动了一下手指。")

    # ── Hunger: still increases during wait ──────────────────────────
    _state.hunger = min(10.0, _state.hunger + 0.5 * elapsed_hours)

    return texts


def _try_souvenir_loss(rng: random.Random) -> str | None:
    """1% chance to lose souvenir (3% when wet/fatigued).

    Card 50: some things lost are lost.
    """
    if _state.souvenir is None:
        return None

    chance = 0.01
    if _state.wet or _state.fatigue > 6.0:
        chance = 0.03

    if rng.random() > chance:
        return None

    name = _state.souvenir.get("name", "东西")
    _state.souvenir = None
    # Record in placememory
    try:
        placememory.record_lost_souvenir(name, _state.place_name or "")
    except AttributeError:
        pass  # placememory may not have this function yet
    return rng.choice(_SOUPY_LOSS_TEXTS).format(name=name)


def _check_storm_block(env: dict) -> str | None:
    """Check if storm blocks walking. Card 50: weather resistance."""
    weather = env.get("weather", {})
    precip = weather.get("precip", "none")
    if precip == "storm" and _state.mode == "land":
        return "雨太大了,你走不了。找地方躲,或者等。"
    return None


def _check_fatigue_slope_block(slope_deg: float) -> str | None:
    """Check if fatigue + steep slope blocks walking. Card 50: body+terrain."""
    if slope_deg > 30.0 and _state.fatigue > 6.0:
        return "这个坡,你现在上不去。歇够了再来,或者绕。"
    return None


def _check_late_night_shop(env: dict) -> bool:
    """Check if it's late night in city (0-5am). Card 50: time resistance."""
    if _state.biome != "city":
        return False
    now = _state.now()
    if now is None:
        return False
    from zoneinfo import ZoneInfo
    tz_name = _tf.timezone_at(lat=_state.pos[0], lng=_state.pos[1]) if _state.pos else None
    if not tz_name:
        return False
    local_hour = now.astimezone(ZoneInfo(tz_name)).hour
    return 0 <= local_hour < 5


def _body_text_for_food_clear(rng: random.Random) -> str:
    """Text when eating clears hunger. Card 50: food satisfaction."""
    _state.hunger = 0.0
    _try_complete_whim("eat", rng)
    return rng.choice(_EAT_CLEAR_TEXTS)


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
        try:
            _SOUVENIRS_BY_PLACE = _json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
        except Exception:
            _SOUVENIRS_BY_PLACE = {}
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


def _filter_ask_hints(sections: list[str]) -> list[str]:
    """Card 52: remove 'ask 能问出更多' when knowledge layer has no content for the name."""
    result = []
    for s in sections:
        if "ask 能问出更多" not in s:
            result.append(s)
            continue
        # Extract person name from "名字。这名字你记下了。ask 能问出更多。"
        m = re.search(r'([一-鿿·]{1,20})。这名字你记下了。ask 能问出更多。', s)
        if m and knowledge.has_knowledge(m.group(1)):
            result.append(s)
        else:
            # Remove hint line, keep rest of section
            cleaned = re.sub(r'\n?[一-鿿·]{1,20}。这名字你记下了。ask 能问出更多。', '', s)
            if cleaned.strip():
                result.append(cleaned)
    return result


@_serialized_action
async def walk_impl(direction: str = "forward", distance_km: float = 2.0) -> dict:
    """Walk one step in the given direction."""
    global _state, _rng, _recent_salience_kinds

    if _state.pos is None:
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}

    # Reset per-walk people encounter flag
    _state.person_encountered_this_walk = False

    # ── Card 50: Pre-step body checks ────────────────────────────────
    # Storm block
    if _state.last_env:
        storm_block = _check_storm_block(_state.last_env)
        if storm_block:
            return {"text": storm_block, "data": {"error": "storm_block"}}

    # Fatigue >9: forced rest
    if _state.fatigue > 9.0:
        forced_text = "你坐下来了。不是你决定的,是身体决定的。你需要歇一歇。"
        return {"text": forced_text, "data": {"error": "forced_rest", "fatigue": _state.fatigue}}

    # ── 1. Parse direction & step ────────────────────────────────────
    bearing, semantic, direction_invalid = _parse_bearing(direction)

    # ── Card 51: toward_sea pre-check — reject if coast > 50 km ────
    if semantic == "toward_sea":
        lat0, lon0 = _state.pos
        sea_km, sea_bearing = walk_mod.nearest_ocean_km_and_bearing(lat0, lon0)
        # Wide scan: if 50km scan found nothing, try up to 5000km (coarser)
        if sea_km is None:
            sea_km, sea_bearing = _wide_coast_scan(lat0, lon0)
        if sea_km is None or sea_km > 50:
            from nowhere.places import _bearing_word as _bw
            dir_str = _bw(sea_bearing) if sea_bearing is not None else "很远"
            if sea_km is not None and sea_km >= 500:
                # Vague text for large distances (Card 51 polish)
                reject_text = _rng.choice(_FAR_COAST_VARIANTS).format(dir=dir_str)
            else:
                dist_str = f"{round(sea_km)}" if sea_km is not None else "很远"
                reject_text = _rng.choice(_SEA_REJECT_VARIANTS).format(
                    dist=dist_str, dir=dir_str,
                )
            return {
                "text": reject_text,
                "data": {
                    "position": {"lat": lat0, "lon": lon0},
                    "rejected": "toward_sea",
                    "nearest_sea_km": sea_km,
                },
            }

    # Card 50: fatigue>6 caps distance to 3km
    _max_dist = walk_mod._DIST_MAX_FATIGUED if _state.fatigue > 6.0 else walk_mod._DIST_MAX
    # Card 64: snapshot timezone before step for jump detection
    _tz_before = _tf.timezone_at(lat=_state.pos[0], lng=_state.pos[1]) if _state.pos else None
    step_result = walk_mod.step(_state, bearing, semantic, distance_km, max_dist=_max_dist)
    # NOTE: time accumulation is handled inside walk.step() using actual
    # distance and speed — do NOT add time here (would double-count).
    # Card 64: detect timezone jump (do NOT smooth — borders are real)
    _tz_after = _tf.timezone_at(lat=_state.pos[0], lng=_state.pos[1]) if _state.pos else None

    # ── Card 51: annotate step_result with water body label ──────────
    if semantic == "toward_sea" and not step_result.get("blocked"):
        dest_surface = step_result.get("new_surface", "")
        _lat_now, _lon_now = _state.pos
        step_result["water_body_label"] = _resolve_water_body_label(
            dest_surface, _lat_now, _lon_now,
        )

    # ── 2. Blocked → render blocked only ─────────────────────────────
    if step_result.get("blocked"):
        reason = step_result.get("reason", "障碍")
        if reason == "water":
            # Honest water blocking: "前面是水面,过不去"
            water_dist = step_result.get("water_distance_km", 0)
            blocked_text = f"前面是水面,过不去。水在{round(water_dist)}公里外。"
        elif reason == "cliff":
            blocked_text = describe.render(
                "blocked", {"reason": "cliff"}, None, _rng,
            )
        else:
            blocked_text = describe.render(
                "blocked", {"reason": reason}, None, _rng,
            )
        return {
            "text": blocked_text,
            "data": {
                "position": {"lat": _state.pos[0], "lon": _state.pos[1]},
                "step": step_result,
            },
        }

    # ── 2b. Card 50: fatigue + steep slope block ─────────────────────
    slope_deg = step_result.get("slope_deg", 0)
    if slope_deg > 0:
        fatigue_slope_block = _check_fatigue_slope_block(slope_deg)
        if fatigue_slope_block:
            return {"text": fatigue_slope_block, "data": {"error": "fatigue_slope_block", "slope_deg": slope_deg, "fatigue": _state.fatigue}}

    # ── 2b. no_gain (uphill on flat terrain) ─────────────────────────
    if step_result.get("no_gain"):
        return {
            "text": "这里无山可爬，四下都是平的。",
            "data": {
                "position": {"lat": _state.pos[0], "lon": _state.pos[1]},
                "step": step_result,
            },
        }

    # ── 2b2. lat_limit: honest latitude boundary ────────────────────
    if step_result.get("lat_limit"):
        from nowhere.walk import _LAT_LIMIT_CLOSINGS
        lat_limit_text = _rng.choice(_LAT_LIMIT_CLOSINGS)
        return {
            "text": lat_limit_text,
            "data": {
                "position": {"lat": _state.pos[0], "lon": _state.pos[1]},
                "step": step_result,
                "lat_limit": True,
            },
        }

    # ── 2c. far_slope: 近处没坡,但高处在远处,先带路 ──────────────────
    _state.radio_steps_since += 1
    _state.walk_step_counter += 1
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

    # Card 64: timezone jump — acknowledge without smoothing
    _tz_jump_note = ""
    if _tz_before and _tz_after and _tz_before != _tz_after:
        _tz_jump_note = _rng.choice(_TZ_JUMP_VARIANTS)

    # ── 3. Gather new point env ──────────────────────────────────────
    lat, lon = _state.pos
    now = _state.now()
    # Snapshot before cache update — _gather_env_cached overwrites _state.last_env
    prev_env = _state.last_env
    # Short-distance mode: skip env fetch, reuse cached (weather/radio unchanged)
    if step_result.get("dist_km", 2.0) < 0.5:
        env = _state.last_env or {}
        env_cached = True
    else:
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
    # Offline waterway lookup (always available, no network needed)
    water_features = _offline_water_nearby(lat, lon, radius_km=50)
    # Try online Overpass as enhancement (silently falls back on failure)
    try:
        online_wf = await asyncio.wait_for(hydrology.nearby_water(lat, lon), timeout=5.0)
        if online_wf:
            water_features = online_wf
    except Exception:
        pass  # offline result already populated

    # Build water feature description from offline data
    if water_features:
        _wf_lat = _state.pos[0] if _state.pos else 0
        _wf_season = describe._season(_state.now().month, _wf_lat) if _state.now() else ""
        water_text = describe.render(
            "water_features", water_features, None, _rng,
            biome=_state.biome or "", elevation=env.get("elevation", 0),
            season=_wf_season, lat=_wf_lat,
        )
        # ── Card 43: water notebook hook ────────────────────────────
        try:
            if water_features:
                _wn = water_features[0].get("name", "") if water_features else ""
                if _wn:
                    _nb_env = dict(env) if env else {}
                    _nb_env["_dt"] = now
                    notebook_mod.record_with_env("water", _wn, _state.place_name or "", _nb_env, lat)
        except Exception:
            pass

    sst_text = ""
    try:
        sst = await asyncio.wait_for(water.sea_surface_temp(lat, lon), timeout=8.0)
        if sst is not None:
            sst_text = water.describe_sst(sst, _rng)
    except Exception:
        pass

    marine_text = ""
    if _rng.random() < 0.3:
        try:
            m = await asyncio.wait_for(water.marine_life(lat, lon, _rng, biome=_state.biome), timeout=8.0)
            if m:
                marine_text = f"{m['common_name']}。{m['distance_m']}米外。{m['scene']}"
        except Exception:
            pass

    # ── Along-river narrative: detect flow alignment ──────────────
    river_text = ""
    if water_features:
        has_river = any(f.get("type") == "river" for f in water_features)
        if has_river:
            river_dir = _compute_river_direction(water_features, lat, lon)
            river_text = _river_alignment_text(bearing, river_dir, _rng)

    # ── 3.6. Density decay: update wilderness depth (Card 40) ────────
    _state.wilderness_depth_km = _compute_wilderness_depth_km(lat, lon)

    # ── 3.7. Density decay: encounter probability tiers (Card 40) ───
    # Within 30km: normal density
    # 30-100km: encounter probability ×0.5, sparse narrative
    # >100km wilderness: encounter ×0.2, "荒深档" rendering
    _wilderness_depth = _state.wilderness_depth_km
    if _wilderness_depth > 100.0:
        _encounter_multiplier = 0.2
        _is_deep_wilderness = True
    elif _wilderness_depth > 30.0:
        _encounter_multiplier = 0.5
        _is_deep_wilderness = False
    else:
        _encounter_multiplier = 1.0
        _is_deep_wilderness = False

    # ── 3.8. Card 50: body state update ─────────────────────────────
    _step_hours = step_result.get("dist_km", 2.0) / 4.0  # approx hours
    body_texts = _update_body_state_walk(env, _step_hours, _rng)
    _state.whim_steps_since += 1

    # Try to emerge a whim
    whim_text = _try_emerge_whim(env, _rng)
    if whim_text:
        body_texts.append(whim_text)

    # ── 5. Salience + describe ───────────────────────────────────────
    # 留白: 缓存命中且世界没变时,跳过 env 候选举的渲染;encounter 照常 roll
    sections: list[str] = []

    # Inject body texts into sections
    for bt in body_texts:
        if bt not in set(_state.recent_scenes):
            sections.append(bt)

    # Try souvenir loss
    souvenir_loss_text = _try_souvenir_loss(_rng)
    if souvenir_loss_text:
        sections.append(souvenir_loss_text)

    # ── 4. message/encounter/wilderness → ACTIONS ────────────────────
    _walk_cc = country.country_code_of(lat, lon)  # always available for sanity_check
    if not env_cached:
        # Card 53: gravity — check if walking near heavy place
        _heavy_nearby_walk = humanities.is_heavy_place(_state.place_name)
        if not _heavy_nearby_walk:
            _h_probe_w = humanities.nearby_place(lat, lon, set(), _rng)
            if _h_probe_w:
                _heavy_nearby_walk = humanities.get_place_weight(_h_probe_w.get("place")) == "heavy"

        candidates = _build_salience_candidates(env, prev_env)
        # Card 69: build Situation for runtime content filtering
        _situation_walk = salience.build_situation(
            lat, lon, _state.place_name or "", env,
            now_month=now.month if now else None,
        )
        top3 = salience.rank(candidates, _rng, recent_kinds=_recent_salience_kinds, intent=_state.intent, heavy_nearby=_heavy_nearby_walk, situation=_situation_walk)
        _recent_salience_kinds = {c["kind"] for c in top3}
        for c in top3:
            prev = None
            if c["kind"] == "terrain" and _state.last_env:
                prev = _last_env_terrain_dict()
            text = describe.render(c["kind"], c["payload"], prev, _rng,
                                   recent_scenes=_state.recent_scenes,
                                   recent_touch=set(_state.recent_touch_sentences))
            if text:
                sections.append(text)
                # Track touch/smell sentences for cross-step dedup
                if c["kind"] == "terrain":
                    surface = c["payload"].get("surface", "")
                    for ts in describe._TOUCH_BY_SURFACE.get(surface, []):
                        if ts in text and ts not in _state.recent_touch_sentences:
                            _state.recent_touch_sentences.append(ts)
                    for bs in describe._SMELL_BY_BIOME.get(_state.biome or "", []):
                        if bs in text and bs not in _state.recent_touch_sentences:
                            _state.recent_touch_sentences.append(bs)
                    # Keep window of 5 (pool min size is 6, ensures 1 fresh)
                    _state.recent_touch_sentences = _state.recent_touch_sentences[-5:]

    if water_text:
        sections.append(water_text)
    if sst_text:
        sections.append(sst_text)
    if marine_text:
        sections.append(marine_text)

    # Card 66: festival atmosphere in walk
    if now:
        _fest_walk = _get_festival_context(
            _state.place_name or "", country.country_code_of(lat, lon), lat, now, lon=lon,
        )
        if _fest_walk:
            _fk = _fest_walk.get("keywords", [])
            if _fk:
                sections.append(f"空气里有{_fk[0]}的味道。节日在身边。")

    # ── 5a. Narrative text from walk discovery (non-Action, stays inline)
    if narrative_text:
        sections.append(narrative_text)

    # ── 5. Action loop (Card 48) ──────────────────────────────────────
    tz_name = _tf.timezone_at(lat=lat, lng=lon)
    local_dt = None
    if tz_name and now is not None:
        local_dt = now.astimezone(ZoneInfo(tz_name))

    # Card 19: Dawn chorus in walk (30% chance during -6..0 sun_alt)
    if not env_cached and _rng.random() < 0.30:
        _sky_w = env.get("sky", {})
        _sa_w = _sky_w.get("sun_alt", 99) if _sky_w else 99
        if _sa_w is None:
            _sa_w = 99
        _dc_text = soundscape.dawn_chorus(_state.biome or "city", _sa_w, _rng)
        if _dc_text:
            sections.append(_dc_text)
    ctx = WalkContext(
        state=_state, env=env, rng=_rng, step_result=step_result,
        lat=lat, lon=lon, now=now, bearing=bearing, semantic=semantic,
        local_dt=local_dt, tz_name=tz_name,
        water_features=water_features,
        is_deep_wilderness=_is_deep_wilderness,
        wilderness_depth=_wilderness_depth,
        encounter_multiplier=_encounter_multiplier,
        env_cached=env_cached, prev_env=prev_env,
        sections=sections,
    )
    _sections_before_actions = len(sections)
    _land_encounter_text = ""
    for act in ACTIONS:
        if act.should(ctx):
            # Card 48: resolve() for side effects before render()
            _resolve = getattr(act, "resolve", None)
            if _resolve:
                _resolve(ctx)
            t = act.render(ctx)
            if t:
                sections.append(t)
                # Card 67: track land encounter for marine exclusion
                if act.name == "encounter":
                    _land_encounter_text = t

    # ── Card 67: marine/land encounter mutual exclusion ───────────────
    # If both marine life and land encounter fired in the same step,
    # drop marine_text (prefer land encounter -- we're walking on land).
    if marine_text and _land_encounter_text:
        try:
            sections.remove(marine_text)
        except ValueError:
            pass

    # ── Card 52: filter "ask" hints — only if knowledge layer has content ──
    sections = _filter_ask_hints(sections)

    # ── Card 40: 3步空转=世界主动给 (Bethesda 30-second rule) ─────────
    _sections_after_actions = len(sections)
    if _sections_after_actions > _sections_before_actions:
        _state.steps_since_content = 0
    else:
        _state.steps_since_content += 1

    if _state.steps_since_content >= 3:
        _forced = _force_content(_is_deep_wilderness, _state.biome, env, _rng)
        if _forced:
            sections.append(_forced)
            _state.steps_since_content = 0

    # ── Card 50: food card hunger clearing ────────────────────────────
    # Scan sections added this step for food-related content
    _food_keywords = {"吃", "饭", "食", "餐", "汤", "面", "肉", "鱼", "菜", "果",
                      "粥", "饺", "包", "饼", "茶", "酒", "咖啡"}
    for s in sections:
        if any(kw in s for kw in _food_keywords):
            if _state.hunger > 0:
                _state.hunger = 0.0
                _try_complete_whim("eat", _rng)
                sections.append(_body_text_for_food_clear(_rng))
            break

    # ── 5a2. Narrative connector (direction change or every 3rd step)
    direction_label = _bearing_to_label(bearing, semantic, water_body_label=step_result.get("water_body_label"))
    if not direction_label and semantic == "forward":
        path_bearing = walk_mod._bearing_from_path(_state.path)
        direction_label = _bearing_to_label(path_bearing, None)
    if direction_label:
        dir_changed = direction_label != _state.narrative.get("direction")
        if dir_changed or _state.walk_step_counter % 3 == 0:
            sections.append(f"你继续往{direction_label}走。")



    # 留白: 缓存命中且无任何 section 命中 → 短句直接返回
    quiet = env_cached and not sections
    ctx.quiet = quiet

    if quiet:
        prose = _rng.choice(_QUIET_WALK)
    else:
        prose = describe.compose(sections, _rng)
        _month = local_dt.month if local_dt else None
        prose = describe.sanity_check(prose, {**env, "_season": describe._season(_month, lat) if _month else "", "_place": _state.place_name or "", "_cc": _walk_cc or ""})
        if far_note:
            prose = far_note + prose
        if sea_note:
            prose += sea_note
        if _tz_jump_note:
            prose += _tz_jump_note
        if direction_invalid:
            prose = f"「{direction}」不是方向，按原方向走了。" + prose
        if step_result.get("clamped"):
            orig = distance_km
            actual = step_result.get("dist_km", 2.0)
            if actual < orig:
                prose = "一步最多 5 公里，按 5 公里走了。" + prose
            else:
                prose = "至少走 50 米，按 50 米算了。" + prose
    # Track recent scene texts for dedup (keep last 5)
    for s in sections:
        if s and len(s) > 10:  # only track substantial texts
            _state.recent_scenes.append(s)
    _state.recent_scenes = _state.recent_scenes[-5:]

    # ── 6. Update state.last_env ─────────────────────────────────────
    _ta_data_walk = _timeaxis_to_env(now, lat, lon) if now else {}
    _state.last_env = {
        "elevation": env.get("elevation"),
        "surface": env.get("surface"),
        "weather": env.get("weather"),
        "sky": env.get("sky"),
        "radio": env.get("radio"),
        "water_features": water_features,
        "timeaxes": _ta_data_walk,
    }
    _state.last_surface = current_surface
    _state.last_elevation = current_elevation

    # ── 7. Post-compose actions (Card 48) ────────────────────────────
    for act in PRE_NORMALIZE_ACTIONS:
        if act.should(ctx):
            t = act.render(ctx)
            if t:
                prose += f"\n{t}"

    prose = describe._normalize_prose(prose)
    _state.last_text = prose
    _record_footprint("walk", prose)

    for act in POST_NORMALIZE_ACTIONS:
        if act.should(ctx):
            t = act.render(ctx)
            if t:
                prose += f"\n{t}"

    _state.save()

    # Card 20: Accumulate distance in per-journey state + global odometer
    _walk_dist = step_result.get("dist_km", 2.0)
    if _walk_dist > 0:
        _state.total_distance_km += _walk_dist
        placememory.add_distance_km(_walk_dist)

    # ── 8. Return ────────────────────────────────────────────────────
    data: dict[str, Any] = {
        "position": {"lat": lat, "lon": lon},
        "step": step_result,
        "weather": env.get("weather"),
        "sky": env.get("sky"),
        "timeaxes": _ta_data_walk if now else {},
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


@_serialized_action
async def listen_impl(seconds: int = 10) -> dict:
    """Listen to the nearest radio station."""
    global _state, _rng

    if _state.pos is None:
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}

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

    # Card 19: Dawn chorus hook — replaces sound during -6..0 window
    _sky_data = (_state.last_env or {}).get("sky", {})
    _sun_alt = _sky_data.get("sun_alt", 99)
    if _sun_alt is None:
        _sun_alt = 99
    _dawn_text = soundscape.dawn_chorus(
        _state.biome or "city", _sun_alt, _rng,
    )
    if _dawn_text:
        sound_text = _dawn_text

    # Card 21: Soundscape credits (20% chance on listen)
    _credit_text = soundscape.soundscape_credit(
        _state.biome or "", _rng,
        listener_lat=lat, listener_lon=lon,
    )

    # ── 1. Find nearest station (sticky) ─────────────────────────────
    station = await _get_radio(lat, lon)
    if not station:
        full_text = sound_text + "收不到电台。"
        if _credit_text:
            full_text += "\n" + _credit_text
        _state.last_text = full_text
        _record_footprint("listen", full_text)
        _state.save()
        return {"text": full_text, "data": {"stream_url": None, "soundscape": sound_text}}

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

    full_text = sound_text + radio_text
    # Card 21: Append soundscape credit if available
    if _credit_text:
        full_text += "\n" + _credit_text
    _state.last_text = full_text
    _record_footprint("listen", full_text, stream_url=stream_url, station=station)
    _state.save()

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


@_serialized_action
async def look_around_impl() -> dict:
    """Walk around the current location and observe.

    Simulates walking 200-500m in a random direction and collecting
    sensory details from multiple sources: local color, soundscape,
    taste/smell, wildlife, art, souvenirs, and messages.
    """
    global _state, _rng

    if _state.pos is None:
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}

    lat, lon = _state.pos
    place = _state.place_name or ""
    now = _state.now()
    sections: list[str] = []

    # ── 1. Start: direction + static observation ───────────────────
    directions = ["东", "南", "西", "北", "东北", "东南", "西北", "西南"]
    direction = _rng.choice(directions)
    _LOOK_STATIC_VERBS = ["目光投向", "视线落在", "你看向", "你望向", "你面朝"]
    verb = _rng.choice(_LOOK_STATIC_VERBS)
    sections.append(f"{verb}{direction}方。")

    # ── 2. Local color (from localcolor.json / baked) ───────────────
    local_hour = None
    cc = None
    tz_name = _tf.timezone_at(lat=lat, lng=lon)
    if tz_name and now is not None:
        local_hour = now.astimezone(ZoneInfo(tz_name)).hour
    cc = country.country_code_of(lat, lon)

    card = localcolor.draw(place, _state.seen_cards, _rng,
                           local_hour=local_hour, country_code=cc, intent=_state.intent,
                           lat=lat, lon=lon, walk_step=_state.walk_step_counter,
                           month=now.month if now else None)
    if card:
        _state.seen_cards.add(card["key"])
        placememory.save_seen_cards(place, _state.seen_cards)
        sections.append(card["text"])

    # ── 3. Soundscape (from scene_soundscape.txt) ───────────────────
    # Card 72: filter seasonal soundscape entries by current season
    soundscapes = _load_scene_file("scene_soundscape")
    if place in soundscapes:
        _ss_pool = soundscapes[place]
        if now is not None:
            _ss_season = describe._season(now.month, lat)
            _ss_seasonal = _get_seasonal_soundscape("scene_soundscape")
            # Build set of seasonal texts for this place that DON'T match current season
            _ss_exclude: set[str] = set()
            for (_sp, _sn), _sdescs in _ss_seasonal.items():
                if _sp == place and _sn != _ss_season:
                    _ss_exclude.update(_sdescs)
            if _ss_exclude:
                _ss_pool = [t for t in _ss_pool if t not in _ss_exclude]
        text = _pick_fresh(_ss_pool, _rng)
        if text:
            sections.append(text)

    # ── 4. Taste/smell (from scene_taste.txt) - 40% chance ──────────
    # Card 82: filter seasonal taste entries by current season (same as soundscape)
    tastes = _load_scene_file("scene_taste")
    if place in tastes and _rng.random() < 0.4:
        _t_pool = tastes[place]
        if now is not None:
            _t_season = describe._season(now.month, lat)
            _t_seasonal = _get_seasonal_soundscape("scene_taste")
            _t_exclude: set[str] = set()
            for (_tp, _tn), _tdescs in _t_seasonal.items():
                if _tp == place and _tn != _t_season:
                    _t_exclude.update(_tdescs)
            if _t_exclude:
                _t_pool = [t for t in _t_pool if t not in _t_exclude]
        text = _pick_fresh(_t_pool, _rng)
        if text:
            sections.append(text)

    # ── 4b. Biome-specific discovery (scene_discovery_{biome}.txt) ──
    # Ensures look_around always has biome content even when no
    # local color / soundscape / taste data exists for the place.
    # Card 72: country-specific pool for city biome (3-tier fallback)
    biome = _state.biome or ""
    if biome:
        disc_pool = []
        if biome == "city" and cc:
            disc_pool = [c["text"] for c in content.cards(f"discovery_city_{cc}")]
        if not disc_pool:
            disc_pool = describe._load_scenes(f"discovery_{biome}")
        if disc_pool:
            biome_disc = _pick_fresh(disc_pool, _rng)
            if biome_disc:
                sections.append(biome_disc)

    # ── 5. Life encounter - 50% chance ──────────────────────────────
    if _rng.random() < 0.5:
        night = (_state.last_env or {}).get("sky", {}).get("phase") == "night"
        weather_text = (_state.last_env or {}).get("weather", {}).get("text", "")
        _BIOME_RADIUS = {"city": 2, "mountain": 10, "volcano": 10, "island": 8, "coast": 8}
        radius = _BIOME_RADIUS.get(_state.biome or "", 15)
        current_month = now.month if now else None
        life_result = await asyncio.wait_for(life.nearby(lat, lon, night=night, weather_text=weather_text,
                                        radius_km=radius, biome=_state.biome, rng=_rng,
                                        month=current_month), timeout=10.0)
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
            # ── Card 43: fauna notebook hook ────────────────────────
            try:
                _fn = life_result.get("common_name") or life_result.get("name", "")
                if _fn:
                    _nb_env = dict(_state.last_env or {})
                    _nb_env["_dt"] = now
                    notebook_mod.record_with_env("fauna", _fn, place, _nb_env, lat)
            except Exception:
                pass

    # ── 6. Art encounter - 30% chance ───────────────────────────────
    if _rng.random() < 0.3:
        mood = (_state.last_env or {}).get("weather", {}).get("precip", "calm")
        if not mood or mood.lower() in ("none", ""):
            mood = "calm"
        art_result = await asyncio.wait_for(art.match(lat, lon, mood, _rng), timeout=10.0)
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
        msg_content = msg["content"] if isinstance(msg, dict) else msg
        if isinstance(msg, dict):
            msg["encountered"] = True
        msg_content = _strip_code_markers(str(msg_content))
        sections.append(f"有人在这里留了句话：「{msg_content}」")

    # ── 9. Ending: static closing (no movement verbs) ──────────────
    _LOOK_CLOSINGS = [
        "你看完了，收回目光。",
        "风把刚才的声音又送了一遍。",
        "你站了一会儿，没动。",
        "远处有什么动了一下，又停了。",
        "你把看到的东西在脑子里过了一遍。",
    ]
    if _rng.random() < 0.6:
        sections.append(_rng.choice(_LOOK_CLOSINGS))

    # ── Compose ─────────────────────────────────────────────────────
    text = "\n".join(sections)
    _state.last_text = text
    _record_footprint("look", text)
    _state.save()
    return {"text": text, "data": {"exploration": True}}


@_serialized_action
async def wait_impl(hours: float = 1.0) -> dict:
    """原地待着,让时间流过去。

    - hours ≤ 12: 逐小时感知（原有模式）
    - hours > 12: "长待"模式，按天出摘要，上限720小时（30天）
    - 任何钳制都在文本里明说，不静默改数
    """
    global _state, _rng

    if _state.pos is None:
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}

    # ── 钳制：上限720h（30天），下限0.25h ────────────────────────────
    _MAX_WAIT = 720.0
    raw_hours = hours
    hours = max(0.25, min(hours, _MAX_WAIT))
    clamped = (raw_hours != hours)
    lat, lon = _state.pos

    # Card 66: track start date for festival crossing detection
    _start_sim_date = None
    if _state.now() is not None:
        _start_sim_date = _state.now().astimezone(ZoneInfo("Asia/Shanghai")).date()

    # ── 长待模式（>12小时）───────────────────────────────────────────
    if hours > 12.0:
        days = int(hours // 24)
        leftover = hours - days * 24

        # Advance clock in bulk
        _state.elapsed_hours += hours
        # Body state: process in 24h chunks
        _is_indoor = _state.biome == "city" and (_state.last_env or {}).get("weather", {}).get("precip", "none") != "storm"
        for _ in range(days):
            _update_body_state_wait(24.0, _is_indoor, _rng)
        if leftover > 0:
            _update_body_state_wait(leftover, _is_indoor, _rng)

        # Gather env once at end
        env, _ = await _gather_env_cached(lat, lon, _state.now())

        # Build day summary
        sections = []
        if days > 0:
            sections.append(f"你在这待了{days}天。")
        if leftover >= 1:
            sections.append(f"又过了{int(leftover)}个小时。")

        # Season snapshot
        end_now = _state.now()
        if end_now:
            end_local = end_now.astimezone(ZoneInfo("Asia/Shanghai"))
            _season_zh = {12: "冬", 1: "冬", 2: "冬", 3: "春", 4: "春", 5: "春",
                          6: "夏", 7: "夏", 8: "夏", 9: "秋", 10: "秋", 11: "秋"}
            sections.append(f"现在是{_season_zh.get(end_local.month, '')}天。")

        # Clamp disclosure
        if clamped:
            if raw_hours > _MAX_WAIT:
                sections.append(f"这个世界一次只肯走{_MAX_WAIT / 24:.0f}天。")
            elif raw_hours < 0.25:
                sections.append("最少也得待一刻钟。")

        text = "\n".join(sections)
        text = describe._normalize_prose(text)

        # Festival crossing detection
        _festival_cross_text = None
        if _start_sim_date is not None and end_now is not None:
            _end_sim_date = end_now.astimezone(ZoneInfo("Asia/Shanghai")).date()
            if _end_sim_date > _start_sim_date:
                cc = country.country_code_of(lat, lon)
                _festival_cross_text = _check_festival_hit(
                    _state.place_name or "", cc, lat, end_now, _rng, lon=lon
                )
        if _festival_cross_text:
            # Use crossing variant if we can extract the festival name
            fest_name = ""
            festivals = _load_festivals()
            if festivals and end_now is not None:
                sim_date = end_now.astimezone(ZoneInfo("Asia/Shanghai")).date()
                for fest in festivals:
                    if _festival_in_window(fest, sim_date, lat):
                        fest_name = fest.get("name", "")
                        break
            if fest_name:
                text = f"{text}\n{_announce_festival_crossing(fest_name, _rng)}"
            else:
                text = f"{text}\n{_festival_cross_text}"

        # Update state
        _state.last_env = {
            "elevation": env.get("elevation"),
            "surface": env.get("surface"),
            "weather": env.get("weather"),
            "sky": env.get("sky"),
            "radio": env.get("radio"),
            "water_features": env.get("water_features"),
        }
        _state.last_text = text
        _record_footprint("wait", text)
        _state.save()

        return {
            "text": text,
            "data": {
                "waited_hours": hours,
                "local_time": _state.now().isoformat() if _state.now() else None,
                "phase": env.get("sky", {}).get("phase"),
                "mode": "long_wait",
            },
        }

    # ── 短待模式（≤12小时）：原有逐小时逻辑 ──────────────────────────
    # Scene file for "sitting still" moments
    _wait_scenes = [
        "你坐着没动。影子挪了方向。",
        "你闭了一下眼睛。再睁开，光不一样了。",
        "你听见自己的呼吸声。比刚才慢了。",
        "你把手放在膝盖上，没动。风在替你走。",
        "你抬头看天。云换了一朵。",
        "你的肩膀松下来了。不知道什么时候松的。",
    ]
    _wait_avail = list(_wait_scenes)  # 不放回抽样池
    _reported_phases: set[str] = set()  # 已报相变去重

    sections: list[str] = []
    prev_env = _state.last_env
    start_temp = (prev_env or {}).get("weather", {}).get("temp_c")
    last_reported_temp = start_temp  # track to avoid repeating the same message
    quiet = True  # 留白: 全程缓存命中且世界没变
    # Card 50: detect if waiting indoors (building/urban and not raining)
    _is_indoor = _state.biome == "city" and (prev_env or {}).get("weather", {}).get("precip", "none") != "storm"
    remaining_hours = hours
    h = 0
    while remaining_hours > 0:
        elapsed_step = min(1.0, remaining_hours)
        _state.elapsed_hours += elapsed_step
        remaining_hours -= elapsed_step
        now = _state.now()

        # Card 50: body state recovery during wait
        wait_body_texts = _update_body_state_wait(elapsed_step, _is_indoor, _rng)
        for wbt in wait_body_texts:
            if wbt not in set(_state.recent_scenes):
                sections.append(wbt)
        env, env_cached = await _gather_env_cached(lat, lon, now)
        if not env_cached:
            quiet = False

        # Sky phase change (only once per transition, deduplicated)
        prev_phase = (prev_env or {}).get("sky", {}).get("phase", "day")
        curr_phase = env.get("sky", {}).get("phase", "day")
        _phase_key = f"{prev_phase}->{curr_phase}"
        if prev_phase != curr_phase and _phase_key not in _reported_phases:
            _reported_phases.add(_phase_key)
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
            if not _wait_avail:
                _wait_avail = list(_wait_scenes)
            _picked = _rng.choice(_wait_avail)
            _wait_avail.remove(_picked)
            sections.append(_picked)

        # Card 42: letter in pack → 10% weight mention during wait
        if _state.errand and _state.errand.get("kind") == "letter" and _rng.random() < 0.10:
            sections.append(errands.letter_wait_text(_rng))

        prev_env = env
        h += 1

    # Card 66: 节日穿越 — wait跨入节日窗口时触发
    _festival_cross_text = None
    if _start_sim_date is not None:
        _end_now = _state.now()
        if _end_now is not None:
            _end_sim_date = _end_now.astimezone(ZoneInfo("Asia/Shanghai")).date()
            if _end_sim_date > _start_sim_date:
                cc = country.country_code_of(lat, lon)
                _festival_cross_text = _check_festival_hit(
                    _state.place_name or "", cc, lat, _end_now, _rng, lon=lon
                )

    # 留白: 缓存命中且世界没变 → 不再逐项描述
    if quiet:
        text = _rng.choice(_QUIET_WAIT)
    else:
        # Rhythm event (what's happening in the city/wild)
        tz_name = _tf.timezone_at(lat=lat, lng=lon)
        if tz_name and _state.now() is not None:
            local_dt = _state.now().astimezone(ZoneInfo(tz_name))
            rhythm = localcolor.rhythm_event(_state.place_name, local_dt.hour, _rng, local_dt.month,
                                        recent=_state.recent_scenes,
                                        weekday=local_dt.weekday())
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
        text = describe._normalize_prose(text)

    # Card 66: append festival crossing text (use crossing variant)
    if _festival_cross_text:
        fest_name = ""
        festivals = _load_festivals()
        if festivals:
            _end_now2 = _state.now()
            if _end_now2 is not None:
                sim_date = _end_now2.astimezone(ZoneInfo("Asia/Shanghai")).date()
                for fest in festivals:
                    if _festival_in_window(fest, sim_date, lat):
                        fest_name = fest.get("name", "")
                        break
        if fest_name:
            text = f"{text}\n{_announce_festival_crossing(fest_name, _rng)}"
        else:
            text = f"{text}\n{_festival_cross_text}"

    # Clamp disclosure for short waits
    if clamped:
        if raw_hours > _MAX_WAIT:
            text = f"{text}\n这个世界一次只肯走{_MAX_WAIT / 24:.0f}天。"
        elif raw_hours < 0.25:
            text = f"{text}\n最少也得待一刻钟。"

    # Update state
    _state.last_env = {
        "elevation": env.get("elevation"),
        "surface": env.get("surface"),
        "weather": env.get("weather"),
        "sky": env.get("sky"),
        "radio": env.get("radio"),
        "water_features": env.get("water_features"),
    }
    _state.last_text = text
    _record_footprint("wait", text)
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
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}
    if not isinstance(topic, str):
        return {"text": "问题必须是文字。", "data": {"error": "bad_topic"}}
    topic = topic.strip()
    if len(topic) > 500:
        return {"text": "问题太长了。", "data": {"error": "topic_too_long"}}

    lat, lon = _state.pos
    result = await asyncio.wait_for(knowledge.about(lat, lon, topic), timeout=10.0)
    if not result and not topic:
        # Place-specific lookup failed; try broader context via place_name
        if _state.place_name:
            result = await asyncio.wait_for(knowledge.about(lat, lon, _state.place_name), timeout=10.0)
    if not result and topic:
        # Try place_name + topic combination (e.g. "京都 金阁寺")
        if _state.place_name and _state.place_name not in topic:
            result = await asyncio.wait_for(knowledge.about(lat, lon, f"{_state.place_name} {topic}"), timeout=10.0)
    if not result:
        return {"text": "关于这个,这里没有留下文字。", "data": {}}

    text = knowledge.voice_layer(result.get("extract", ""), _rng)
    _record_footprint("ask", text)
    return {"text": text, "data": result}


@_serialized_action
async def walk_to_impl(place: str) -> dict:
    """朝一个命名地点走。RDR2式旅程叙事：路线预计算→关键节点→到达仪式。"""
    global _state, _rng

    if _state.pos is None:
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}

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
    total_km = 0.0
    max_steps = max(3, min(10, int(dist / 5) + 1))
    # last_env is always flat format: {elevation, surface, sky, weather, ...}
    last_env = _state.last_env or {}
    last_surface = last_env.get("surface", "")
    terrain_changes = 0

    while steps < max_steps:
        lat, lon = _state.pos
        remaining = places._haversine_km(lat, lon, target["lat"], target["lon"])
        if remaining < 1.0:
            break

        bearing_deg = places._bearing_deg(lat, lon, target["lat"], target["lon"])
        step_km = min(5.0, remaining)
        step_result = walk_mod.step(_state, bearing_deg, None, step_km)
        steps += 1
        total_km += step_km

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
            f"到了。{place}。你走了{total_km:.0f}公里。远处有炊烟，你知道到家了。",
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
    # NOTE: time accumulation is handled inside walk.step() per step — do NOT add here.
    now = _state.now()
    lat, lon = _state.pos
    env, _ = await _gather_env_cached(lat, lon, now)
    _state.last_env = {
        "elevation": env.get("elevation"),
        "surface": env.get("surface"),
        "weather": env.get("weather"),
        "sky": env.get("sky"),
        "radio": env.get("radio"),
        "water_features": env.get("water_features"),
    }
    _state.last_surface = env.get("surface", "")
    _state.last_elevation = env.get("elevation", 0)

    text = "\n".join(lines)
    text = describe._normalize_prose(text)
    _state.last_text = text
    _record_footprint("walk_to", text)
    _state.save()

    # Card 20: Accumulate distance in global odometer
    if total_km > 0:
        placememory.add_distance_km(total_km)

    return {
        "text": text,
        "data": {"target": target, "arrived": arrived, "steps": steps, "remaining_km": round(remaining, 1)},
    }


def mark_impl(name: str, note: str = "", overwrite: bool = False) -> dict:
    """Save current position as a named bookmark."""
    global _state

    if _state.pos is None:
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}

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
    text = f"已标记「{name}」。"
    _record_footprint("mark", text)
    return {
        "text": text,
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
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}

    lat, lon = _state.pos
    utc_now = _state.now()

    parts: list[str] = []
    _blind = getattr(_state, "blind", False)
    if _blind:
        parts.append("你在某个地方。")
    elif _state.place_name:
        parts.append(f"你在{_state.place_name}。")
    if not _blind:
        parts.append(f"坐标 {lat:.4f}, {lon:.4f}。")
        from nowhere import terrain as _elev_mod
        _elev = _elev_mod.elevation(lat, lon, place_name=_state.place_name or "")
        if _elev and _elev > 2:
            parts.append(f"海拔 {_elev:.0f} 米。")
    else:
        parts.append(f"走了 {len(_state.path)} 步,出门 {_state.elapsed_hours:.1f} 小时。")
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

    # Wilderness depth reporting (Card 40: honest boundaries)
    if _state.wilderness_depth_km > 100.0:
        parts.append(f"荒野深处。最近的已知地点在{_state.wilderness_depth_km:.0f}公里外。")
    elif _state.wilderness_depth_km > 30.0:
        parts.append(f"人迹罕至。最近的已知地点在{_state.wilderness_depth_km:.0f}公里外。")

    # Card 42: errand hint
    errand_hint = errands.errand_hint_line(_state.errand)
    if errand_hint:
        parts.append(errand_hint)

    # Card 20: Odometer (per-journey)
    total_km = _state.total_distance_km
    if total_km >= 1.0:
        parts.append(f"这趟出门,你已经走了 {total_km:.0f} 公里。")
    elif total_km > 0:
        parts.append("还没走出一条街。")

    return {
        "text": "".join(parts),
        "data": {
            "position": {"lat": lat, "lon": lon},
            "place_name": _state.place_name,
            "landed_at": _state.landed_at.isoformat() if _state.landed_at else None,
            "elapsed_hours": _state.elapsed_hours,
            "steps": len(_state.path),
            "mode": _state.mode,
            "wilderness_depth_km": _state.wilderness_depth_km,
            "providers": providers.provider_status(),
        },
    }


# ── Card 57: coastal elevation clamping ──────────────────────────────


def _clamp_coastal_elevation(elev: float, surface: str, lat: float, lon: float) -> float:
    """Coarse-grid (1°) coastline cells produce garbage elevations.

    Root cause: grid_tiny merges land and ocean in the same 1° cell;
    the averaged elevation can be wildly off — e.g. Weihai reports 300 m
    while sitting at sea level.  Same disease as card 26.

    Diagnostic signal: if all 8 grid neighbours share the exact same
    elevation, the grid has no real terrain data for this cell — it is
    a coarse default (typically 200-300 m).  Real terrain (even flat
    plains) always shows some variation at 1° resolution.

    Strategy:
    1. Water surface → always near sea level (0-5 m).
    2. High-res tile available → trustworthy, skip.
    3. Flat neighbours on coarse grid → elevation is a grid artifact.
       Look up the nearest large city in cities15000: if within 30 km,
       use its population as a proxy for "this is a significant place
       where the coarse grid is misleading".  Clamp to 0-50 m.
    4. Otherwise → leave untouched (preserves Denver, Lhasa, etc.).

    Trade-off: this also clamps some inland cities (Moscow ~156 m → 50 m)
    when the grid provides no real terrain data.  Proper fix is high-res
    SRTM tiles for all major cities (card 26).
    """
    # Water: always sea level
    if surface in ("water_ocean", "water_fresh", "wetland"):
        return max(0.0, min(elev, 5.0))

    # High-res tile available → data is trustworthy, skip clamping
    if terrain._find_tile(lat, lon) is not None:
        return elev

    # Check if all 8 grid neighbours have the same elevation —
    # this indicates the 1° grid has no real terrain data.
    step = 1.0
    neighbour_elevs: set[float] = set()
    for dlat in (-step, 0, step):
        for dlon in (-step, 0, step):
            if dlat == 0 and dlon == 0:
                continue
            neighbour_elevs.add(terrain.elevation(lat + dlat, lon + dlon))
    is_flat_grid = len(neighbour_elevs) == 1

    if not is_flat_grid:
        # Real terrain variation — elevation is meaningful
        return elev

    # Flat grid: elevation is a coarse default.  Check if a significant
    # city is nearby (cities15000 pop > 100k within 30 km).
    cos_lat = math.cos(math.radians(lat))
    try:
        with open(_PACK_PATH, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 15:
                    continue
                try:
                    clat = float(parts[4])
                    clon = float(parts[5])
                    pop = int(parts[14] or 0)
                except ValueError:
                    continue
                if pop < 100_000:
                    continue
                dlat = clat - lat
                dlon = clon - lon
                if dlon > 180:
                    dlon -= 360
                elif dlon < -180:
                    dlon += 360
                dist_km = 111.0 * math.sqrt(dlat ** 2 + (dlon * cos_lat) ** 2)
                if dist_km < 30:
                    # Significant city on flat-grid cell → clamp
                    return max(0.0, min(elev, 50.0))
    except Exception:
        logger.debug("_clamp_coastal_elevation lookup failed", exc_info=True)

    return elev


# ── Card 57: localized stamp place names ─────────────────────────────

_COUNTRY_TO_SCRIPT: dict[str, tuple[str, list[str]]] = {
    "JP": ("han", ["jp"]),       "KR": ("hangul", []),         "TH": ("thai", []),
    "RU": ("cyrillic", []),      "UA": ("cyrillic", ["uk"]),   "BY": ("cyrillic", []),
    "EG": ("arabic", []),        "SA": ("arabic", []),         "AE": ("arabic", []),
    "IL": ("hebrew", []),        "IN": ("devanagari", []),     "BD": ("bengali", []),
    "GR": ("greek", []),         "GE": ("georgian", []),
    # Multi-language countries: use first official language (latin)
    "CH": ("latin", []),         "BE": ("latin", []),          "CA": ("latin", []),
}

_PACK_PATH = _pathlib.Path(__file__).resolve().parent / "data" / "packs" / "cities15000.txt"

_local_name_cache: dict[str, str] = {}


def _char_script(ch: str) -> str:
    """Classify a character into broad script buckets."""
    cp = ord(ch)
    if 0x3040 <= cp <= 0x30FF or (0xFF65 <= cp <= 0xFF9F):
        return "kana"
    if 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F:
        return "hangul"
    if 0x0E00 <= cp <= 0x0E7F:
        return "thai"
    if 0x0900 <= cp <= 0x097F:
        return "devanagari"
    if 0x0980 <= cp <= 0x09FF:
        return "bengali"
    if 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or 0xFB50 <= cp <= 0xFEFF:
        return "arabic"
    if 0x0590 <= cp <= 0x05FF:
        return "hebrew"
    if 0x0400 <= cp <= 0x04FF:
        return "cyrillic"
    if 0x0370 <= cp <= 0x03FF:
        return "greek"
    if 0x10A0 <= cp <= 0x10FF or 0x2D00 <= cp <= 0x2D2F:
        return "georgian"
    if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
        return "han"
    # Latin (including extended-A/B for accented chars like è, ə)
    if (0x0041 <= cp <= 0x005A or 0x0061 <= cp <= 0x007A
            or 0x00C0 <= cp <= 0x024F or 0x1E00 <= cp <= 0x1EFF):
        return "latin"
    return "other"


def _has_cjk(text: str) -> bool:
    return any(0x4E00 <= ord(c) <= 0x9FFF or 0x3400 <= ord(c) <= 0x4DBF for c in text)


def _pick_best_localized(
    candidates: list[str],
    script: str,
    cc: str,
    place_name: str,
    target_info: tuple | None,
    ascii_name: str = "",
) -> str | None:
    """Pick the best localized name from a list of same-script alternates.

    GeoNames alternatenames are ordered roughly by importance.  We preserve
    that order and apply targeted filters per script to skip abbreviations,
    vowelized forms, and other non-standard variants, then return the first
    surviving candidate.
    """
    if not candidates:
        return None

    def _acceptable(alt: str) -> bool:
        n = len(alt)
        # Minimum length: CJK/Hangul names can be 2 chars (東京, 서울)
        min_len = 2 if script in ("han", "hangul", "kana") else 3
        if n < min_len:
            return False

        if script == "latin":
            # Skip all-uppercase abbreviations (GVA, CAI, SEL, BOM)
            if alt.isupper() and n <= 4:
                return False

        if script == "hebrew":
            # Skip vowelized forms (niqqud diacritics U+0591-U+05BD)
            if any(0x0591 <= ord(c) <= 0x05BD for c in alt):
                return False
            # Skip very short ancient names (Jebus, Salem, Zion)
            if n < 5:
                return False
            if n > 12:
                return False

        if script == "devanagari":
            # Skip compound names with "Greater" prefix
            if n > 10:
                return False

        if script == "cyrillic":
            # Skip mixed-script transliterations (contain Latin chars)
            if any(_char_script(c) == "latin" for c in alt):
                return False

        if script == "hangul":
            # Skip long compound names (서울특별시, 한양 etc.)
            if n > 6:
                return False

        return True

    # First pass: apply filters
    filtered = [a for a in candidates if _acceptable(a)]

    # For Cyrillic with language preference (UA): among filtered, pick
    # the one with language-specific chars
    if script == "cyrillic" and target_info and len(target_info) > 1:
        lang_chars = {"uk": set("їЇєЄґҐ"), "ru": set("ёЁъЪ")}
        for lang in target_info[1]:
            chars = lang_chars.get(lang, set())
            for alt in filtered:
                if chars & set(alt):
                    return alt

    # For han + JP: prefer traditional form (東京 over 东京)
    if script == "han" and cc == "JP":
        for alt in filtered:
            if alt != place_name and _has_cjk(alt):
                return alt

    # For Latin: prefer accented endonyms (Genève > Genf > Geneva)
    if script == "latin":
        accented = [a for a in filtered if any(0x00C0 <= ord(c) <= 0x024F for c in a)]
        if accented:
            return accented[0]

    # For Devanagari: prefer names matching ASCII first syllable
    # (Mumbai → मुंबई over बम्बई; Delhi → दिल्ली)
    if script == "devanagari" and filtered:
        if ascii_name.lower().startswith("mu"):
            mu_names = [a for a in filtered if a.startswith("मु")]
            if mu_names:
                return mu_names[0]

    # For Hangul: prefer names matching ASCII first syllable
    # (Seoul → 서울 over 경성; Busan → 부산)
    if script == "hangul" and filtered:
        # Prefer the2-char "clean" name (서울, 부산) over compounds
        short = [a for a in filtered if len(a) == 2]
        if short:
            # Match first syllable: ASCII "Se" → "서", "Bu" → "부"
            _HANGUL_INIT = {
                "se": "서", "bu": "부", "in": "인", "da": "대", "gw": "광",
                "je": "제", "ch": "천", "su": "수", "ul": "울", "gy": "경",
            }
            prefix = ascii_name.lower()[:2]
            expected_init = _HANGUL_INIT.get(prefix)
            if expected_init:
                matching = [a for a in short if a.startswith(expected_init)]
                if matching:
                    return matching[0]
            return short[0]

    # For Cyrillic + RU: prefer "Мо-" prefix for "Mo-" cities
    if script == "cyrillic" and filtered and cc == "RU":
        if ascii_name.lower().startswith("mo"):
            mo_names = [a for a in filtered if a.startswith("Мо")]
            if mo_names:
                return mo_names[0]

    # Default: first surviving candidate (GeoNames order)
    return filtered[0] if filtered else candidates[0]


def _localized_place_name(place_name: str, lat: float, lon: float) -> str:
    """Return the local-language name for a place on the postcard stamp.

    cities15000.txt ``name`` column is always romanised (Tokyo, Moscow, Cairo).
    The local-script name lives in ``alternatenames`` (col 3).

    Logic:
    1. Find the nearest city entry matching ``place_name``.
    2. Determine target script from country code.
    3. Pick the best alternename in that script.
    4. Fallback chain: target-script alt → CJK alt → romanised name.

    For China/HK/TW/JP: place_name itself is already CJK, pass through.
    For all other countries: return local script (東京/Москва/القاهرة…).
    Romanised fallback is normal for foreign postcards; Chinese stamp on
    a Tokyo postcard is the real bug.
    """
    if not place_name or not _PACK_PATH.exists():
        return place_name

    key = f"{place_name}|{lat:.2f}|{lon:.2f}"
    if key in _local_name_cache:
        return _local_name_cache[key]

    # CJK input in CJK-speaking region → already correct, pass through.
    # JP needs lookup (simplified→traditional), handled below.
    cc = country.country_code_of(lat, lon)
    if cc in ("CN", "TW", "HK", "MO") and _has_cjk(place_name):
        _local_name_cache[key] = place_name
        return place_name

    # Scan cities15000 for the matching city
    q = place_name.strip().lower()
    deg = 0.5  # ~55 km search radius
    best_entry: dict | None = None
    best_dist = float("inf")

    with open(_PACK_PATH, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 15:
                continue
            try:
                clat, clon = float(parts[4]), float(parts[5])
            except ValueError:
                continue
            dlat = clat - lat
            dlon = clon - lon
            if dlon > 180:
                dlon -= 360
            elif dlon < -180:
                dlon += 360
            if abs(dlat) > deg or abs(dlon) > deg:
                continue
            name_l = parts[1].lower()
            asciiname_l = parts[2].lower()
            alts = parts[3]
            # Match: exact name, asciiname, or alternename (check all)
            matched = (q == name_l or q == asciiname_l
                       or any(q == a.strip().lower() for a in alts.split(",")))
            # CJK fuzzy: simplified/traditional may differ (基辅 vs 基輔)
            if not matched and _has_cjk(place_name):
                q_chars = set(place_name)
                for a in alts.split(","):
                    a = a.strip()
                    if _has_cjk(a):
                        a_chars = set(a)
                        overlap = len(q_chars & a_chars)
                        if overlap >= max(1, len(q_chars) * 0.5):
                            matched = True
                            break
            if not matched:
                continue
            dist = dlat * dlat + dlon * dlon
            if dist < best_dist:
                best_dist = dist
                best_entry = {
                    "name": parts[1],
                    "asciiname": parts[2],
                    "alternatenames": parts[3],
                    "cc": parts[8],
                    "admin1": parts[10] if len(parts) > 10 else "",
                }

    if best_entry is None:
        # No city found: if input is CJK, keep it; else return as-is
        result = place_name
        _local_name_cache[key] = result
        return result

    e_cc = best_entry["cc"]
    alts_str = best_entry["alternatenames"]
    alt_list = [a.strip() for a in alts_str.split(",") if a.strip()]
    admin1 = best_entry.get("admin1", "")

    # Classify alternates by script.
    # An alt is assigned to a script only if ≥60% of non-space, non-punctuation
    # characters belong to that script.  This filters out transliterations
    # like "Məskəү" (1 Cyrillic char among 6 Latin).
    script_groups: dict[str, list[str]] = {}
    for alt in alt_list:
        counts: dict[str, int] = {}
        total_alpha = 0
        for ch in alt:
            if ch.isspace():
                continue
            s = _char_script(ch)
            if s != "other":
                counts[s] = counts.get(s, 0) + 1
                total_alpha += 1
        if not total_alpha:
            continue
        dominant = max(counts, key=lambda k: counts[k])
        # Require ≥60% dominance (filters mixed-script transliterations)
        if counts[dominant] / total_alpha >= 0.6:
            script_groups.setdefault(dominant, []).append(alt)

    # Determine what the input is
    input_scripts: set[str] = set()
    for ch in place_name:
        s = _char_script(ch)
        if s != "other":
            input_scripts.add(s)

    # Country → target script
    target_info = _COUNTRY_TO_SCRIPT.get(e_cc)
    target_script = target_info[0] if target_info else None

    result: str | None = None

    ascii_nm = best_entry.get("asciiname", "")

    if target_script and target_script in script_groups:
        candidates = script_groups[target_script]
        result = _pick_best_localized(candidates, target_script, e_cc,
                                      place_name, target_info, ascii_nm)
    elif "latin" in script_groups:
        result = _pick_best_localized(script_groups["latin"], "latin",
                                      e_cc, place_name, target_info, ascii_nm)

    if result is None:
        # Foreign country, no script match: use romanised name
        result = best_entry["asciiname"] or best_entry["name"]

    _local_name_cache[key] = result
    return result


def _postmark(lat: float, lon: float) -> dict:
    """邮戳保留旅程内当地时间；现实寄出时间由明信片另行记录。"""
    # Always use terrain module for stamp elevation — last_env elevation
    # comes from the coarse grid which can be wildly off for coastal cities
    # (e.g. Weihai reports 300 m while sitting at sea level).  The terrain
    # module checks DEM tiles first, which is more accurate.
    elev = terrain.elevation(lat, lon, _state.place_name or "")
    # Card 57: clamp coastal elevation (coarse-grid coastline bug)
    surface = _last_env_surface() or "grass"
    elev = _clamp_coastal_elevation(elev, surface, lat, lon)

    # Card 57: localized place name for stamp
    raw_place = _state.place_name or f"{lat:.2f}, {lon:.2f}"
    stamp_place = _localized_place_name(raw_place, lat, lon)

    stamp: dict = {
        "place": stamp_place,
        "place_zh": raw_place if (stamp_place != raw_place and raw_place not in stamp_place and stamp_place not in raw_place) else None,
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "elevation": round(elev),
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


def _record_footprint(
    action: str,
    text: str,
    *,
    stream_url: str | None = None,
    station: dict | None = None,
) -> None:
    """记录一条可见旅行足迹，不与 WorldState 的存档周期耦合。"""
    if _state.pos is None or not text:
        return
    placememory.record_footprint(
        action,
        text,
        _state.pos[0],
        _state.pos[1],
        _state.place_name,
        stream_url=stream_url,
        station=station,
    )


# ── Farewell / Return helpers (card 27: peak-end) ────────────────────


def _generate_farewell(state: state_mod.WorldState, rng: random.Random) -> str:
    """Generate farewell text when leaving a journey.

    Uses current env for a "last glimpse" snapshot, then appends a body
    farewell sentence from the variant pool.
    """
    env = state.last_env or {}
    weather = env.get("weather") or {}
    sky = env.get("sky") or {}

    parts: list[str] = []

    # Last glimpse: weather snapshot
    weather_text = weather.get("text", "")
    if weather_text:
        parts.append(f"此刻{weather_text}。")

    # Farewell body from variant pool
    phase = sky.get("phase", "day")
    phase_desc = describe._TIME_LABELS.get(phase, "白天")
    farewell_tmpl = rng.choice(describe._FAREWELL_VARIANTS)
    farewell = farewell_tmpl.format(
        place=state.place_name or "这里",
        phase_desc=phase_desc,
    )
    parts.append(farewell)

    return "".join(parts)


def _generate_return(
    state: state_mod.WorldState, meta: dict | None, rng: random.Random
) -> str:
    """Generate return text when coming back to a journey.

    Calculates real-world elapsed time since departure and compares seasons.
    Returns empty string if not enough time has passed for a meaningful note.
    """
    if not meta or not meta.get("departed_at"):
        return ""

    # Calculate real-world elapsed time
    departed_at = datetime.fromisoformat(meta["departed_at"])
    if departed_at.tzinfo is None:
        departed_at = departed_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    elapsed = now - departed_at

    # Only mention return if significant time has passed (> 1 hour)
    if elapsed.total_seconds() < 3600:
        return ""

    # Calculate season change: old season from journey's simulated time,
    # new season from real-world time (the world continued while you were away)
    lat = state.pos[0] if state.pos else 0
    old_time = state.now()
    old_month = old_time.month if old_time else departed_at.month
    new_month = now.month

    old_season_zh = describe._SEASON_EN_TO_ZH.get(describe._season(old_month, lat), "")
    new_season_zh = describe._SEASON_EN_TO_ZH.get(describe._season(new_month, lat), "")

    # Mention season change if different
    if old_season_zh and new_season_zh and old_season_zh != new_season_zh:
        return_tmpl = rng.choice(describe._RETURN_VARIANTS)
        return return_tmpl.format(old_season=old_season_zh, new_season=new_season_zh)

    # Even if same season, mention elapsed time if > 1 day
    if elapsed.days > 0:
        return f"你离开了 {elapsed.days} 天。世界没有停。"

    return ""


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
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}
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
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "replies": [],
        "front_img": None,  # 异步生成,好了挂上;没有就前端 SVG 兜底
    }
    _state.postcards.append(card)
    placememory.save_postcard(card)  # 落盘: 文件是真相,网页旁观者看得见
    _record_footprint("postcard", text)
    _state.save()
    _poster_front_async(card, lat, lon)

    s = card["stamp"]

    # ── 正面画面: 旅程优先,地表兜底 ──────────────────────────────
    surface = _last_env_surface() or "grass"
    phase = (_state.last_env or {}).get("sky", {}).get("phase", "day")
    elev = s["elevation"]
    weather_text = s.get("weather", "")
    temp = s.get("temp_c", "")

    # Card 57: 正面优先用旅程已见卡的环境句——明信片长在这次旅程上
    import hashlib as _hashlib
    journey_front: str | None = None

    # 1) 从 localcolor 已见卡的 text 里挑一句环境描写
    place = _state.place_name
    if place:
        _lc_cards = localcolor._load()
        seen_texts = [
            c.text for c in _lc_cards
            if c.conditions.get("place") == place and c.id in _state.seen_cards
            and c.meta.get("category") != "节律"
        ]
        if seen_texts:
            idx = int(_hashlib.md5(f"postcard_{card['id']}".encode()).hexdigest()[:4], 16) % len(seen_texts)
            candidate = seen_texts[idx]
            # 取第一句(不超 60 字),太长截断
            first_sent = candidate.split("。")[0].split("\n")[0].strip()
            if len(first_sent) > 60:
                first_sent = first_sent[:58] + "……"
            if first_sent:
                journey_front = first_sent + "。"

    # 2) 没有已见卡 → 用当前环境实况拼一句画面
    if journey_front is None:
        env = _state.last_env or {}
        env_parts: list[str] = []
        if weather_text:
            env_parts.append(weather_text)
        if surface and surface not in ("urban",):
            _SURFACE_WORD = {
                "forest": "林子里", "rock": "岩壁下", "sand": "沙地上",
                "grass": "草地上", "snow": "雪地里", "ice": "冰面上",
                "bare": "碎石地上", "water_ocean": "海边", "water_fresh": "水边",
                "wetland": "湿地里",
            }
            sw = _SURFACE_WORD.get(surface, "")
            if sw:
                env_parts.append(sw)
        if env_parts:
            journey_front = "、".join(env_parts) + "。"

    # 3) 全兜底: 地表固定池(从未见过的新地方)
    surface_snapshots: dict[str, list[str]] = {
        "forest": ["树冠挨着树冠,绿的深浅分了好几层。阳光从叶子缝里漏下来,在地上碎成金点。","树一层一层地叠上去,深绿压着浅绿。林间有雾,薄薄的一层。","一棵老树横在画面里,树干上长满了蕨。"],
        "urban": ["房子挤着房子,阳台上的衣服在风里晃。远处有楼的轮廓。","窗台上摆着一盆花,不知道什么品种。叶子在风里动了一下。"],
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
    if journey_front is None:
        surface_choices = surface_snapshots.get(surface, surface_snapshots["bare"])
        surf_idx = int(_hashlib.md5(f"postcard_{card['id']}".encode()).hexdigest()[:4], 16) % len(surface_choices)
        journey_front = surface_choices[surf_idx]
    front_image = journey_front

    # ── 背面邮戳 ──────────────────────────────────────────────────────
    lat_dir = "北纬" if s["lat"] >= 0 else "南纬"
    lon_dir = "东经" if s["lon"] >= 0 else "西经"
    # Card 57: RTL文字(阿拉伯/希伯来)单独一行,不与拉丁混排
    _RTL_RANGES = (
        (0x0590, 0x05FF),  # Hebrew
        (0x0600, 0x06FF),  # Arabic
        (0x0750, 0x077F),
        (0xFB50, 0xFDFF),
        (0xFE70, 0xFEFF),
    )
    place_text = s['place']
    is_rtl = any(
        any(lo <= ord(ch) <= hi for lo, hi in _RTL_RANGES)
        for ch in place_text
    )
    if is_rtl:
        stamp_describe = (
            f"明信片正面: {front_image} "
            f"翻过来,邮戳是圆的,印着——"
            f"\n{place_text}"
            f"\n{lat_dir}{abs(s['lat']):.1f}°,{lon_dir}{abs(s['lon']):.1f}°。"
            f"海拔{elev}米。{s['local_time']}。"
        )
    else:
        stamp_describe = (
            f"明信片正面: {front_image} "
            f"翻过来,邮戳是圆的,印着——"
            f"{place_text}。{lat_dir}{abs(s['lat']):.1f}°,{lon_dir}{abs(s['lon']):.1f}°。"
            f"海拔{elev}米。{s['local_time']}。"
        )
    # Card 57: 中文名括注(背面小字)
    zh_name = s.get("place_zh")
    if zh_name and zh_name != place_text:
        stamp_describe += f"({zh_name})"

    # ── Card 42: 邮差差事 — 寄完明信片 10% 拿到一封信 ────────────
    if (not _state.errand
            and not _state.errand_letter_taken_this_journey
            and _rng.random() < 0.10):
        _llat, _llon = _state.pos if _state.pos else (0.0, 0.0)
        letter = errands.pick_letter(_rng, listener_lat=_llat, listener_lon=_llon)
        if letter:
            _state.errand = errands.take_letter(letter, _state.now() or datetime.now(timezone.utc))
            _state.errand_letter_taken_this_journey = True
            _state.save()
            stamp_describe += (
                f"\n寄完明信片,柜台后面的人递过来一封信。"
                f"「给{letter['recipient']}的。{letter['sender']}托的。」"
                f"你把信揣进包里。"
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
            _state.save()
            return {"ok": True}
    if placememory.add_postcard_reply(card_id, content):
        _state.messages.append({"content": f"[回信] {content}", "encountered": False})
        _state.save()
        return {"ok": True}
    return {"ok": False, "error": "no such postcard"}


# =====================================================================
# MCP tool wrappers (thin shells around _impl)
# =====================================================================


@mcp.tool()
async def open_door(to: str | None = None, blind: bool = False, key: str | None = None, intent: str | None = None) -> dict:
    """Open the door.  No arg = random landing; pass a place name or bookmark name.
    blind=True: hide place name (guess to reveal).
    key="...": deterministic landing by key (same key = same place).
    intent biases what you see (e.g. "吃" boosts food, "孤独" boosts quiet).
    Append " 新" to place name (e.g. "拉萨 新") to force a fresh landing,
    creating a new journey even if one already exists for that place.
    """
    return await open_door_impl(to, blind=blind, key=key, intent=intent)


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
def bury(note: str | None = None) -> dict:
    """把身上的东西埋在当前坐标。可以留一句话。"""
    return bury_impl(note)


def bury_impl(note: str | None = None) -> dict:
    """Bury the current souvenir underground."""
    global _state
    if _state.pos is None:
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}
    if _state.souvenir is None:
        return {"text": _rng.choice(_EMPTY_BURY_VARIANTS), "data": {"error": "empty"}}

    s = _state.souvenir
    sanitized_note = ""
    if note:
        sanitized_note = _sanitize_external(note).strip("「」")

    entry = {
        "name": s.get("name", ""),
        "desc": s.get("desc", ""),
        "from": s.get("from", ""),
        "pos": list(_state.pos),
        "buried_at": datetime.now(timezone.utc).isoformat(),
        "note": sanitized_note,
    }
    placememory.save_buried(entry)
    _state.souvenir = None

    text = _rng.choice(_BURY_VARIANTS).format(name=s.get("name", ""))
    if sanitized_note:
        text += f" 留了一句话:{_sanitize_external(note)}"
    _record_footprint("bury", f"埋下了{s.get('name', '')}")
    _state.save()
    return {"text": text, "data": {"buried": entry}}


@mcp.tool()
def deliver() -> dict:
    """送达身上的差事(信/铁盒)。需要在收信地附近(5km)。"""
    return deliver_impl()


def deliver_impl() -> dict:
    """送达差事。"""
    global _state

    if _state.pos is None:
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}
    if _state.errand is None:
        return {"text": "身上没有差事。", "data": {"error": "no_errand"}}

    kind = _state.errand.get("kind")

    if kind == "letter":
        # Build place coords from explorable_index
        place_coords = _build_place_coords()
        matched = errands.check_delivery(
            _state.pos, _state.errand, place_coords, radius_km=5.0,
        )
        if not matched:
            hint = _state.errand.get("hint", "")
            return {
                "text": f"还没到。这封信要送到{hint}的地方。再走走。",
                "data": {"delivered": False},
            }
        # Delivered
        now = _state.now() or datetime.now(timezone.utc)
        journal_entry = errands.build_delivery_journal(
            _state.errand, matched,
            _state.errand.get("taken_at", now.isoformat()), now,
        )
        sender = _state.errand.get("sender", "无名")
        recipient = _state.errand.get("recipient_desc", "")
        text = (
            f"你把信交给了{matched}的人。「{sender}托的。」"
            f"对方接过信,点了点头。"
            f"\n{journal_entry}"
        )
        _state.errand = None
        _state.journey_log.append({
            "kind": "delivery",
            "text": journal_entry,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        _record_footprint("deliver", journal_entry)
        _state.save()
        return {"text": text, "data": {"delivered": True, "place": matched, "journal": journal_entry}}

    if kind == "chain":
        # Chain delivery: bury at current spot advances the chain
        text = "铁盒埋下了。里面有一张字条。"
        _state.errand = None
        _state.save()
        return {"text": text, "data": {"delivered": True, "chain": True}}

    return {"text": "不知道怎么处理这个差事。", "data": {"error": "unknown_kind"}}


def _build_place_coords() -> dict[str, tuple[float, float]]:
    """Build a place→coords map from explorable_index for errand delivery."""
    try:
        data = _load_explorable_index()
        coords = {}
        for name, info in data.get("places", {}).items():
            plat = info.get("lat")
            plon = info.get("lon")
            if plat is not None and plon is not None:
                coords[name] = (plat, plon)
        return coords
    except Exception:
        return {}


@mcp.tool()
def postcards() -> dict:
    """看看收到的明信片。来自不同时空的问候。"""
    cards = _state.postcards
    if not cards:
        return {"text": "还没收到过明信片。空空的。", "data": {"postcards": []}}
    parts = []
    for c in cards:
        stamp = c.get("stamp", {})
        who = stamp.get("place", "远方")
        msg = c.get("text", "")
        time_str = stamp.get("local_time", "")
        parts.append(f"来自{who}（{time_str}）：{msg}")
    text = f"你收到了 {len(cards)} 张明信片。\n" + "\n---\n".join(parts)
    return {"text": text, "data": {"postcards": cards}}


@mcp.tool()
async def walk_to(place: str) -> dict:
    """朝一个命名地点走过去(山/河/城/古迹)。探索从此有方向。"""
    return await walk_to_impl(place)


@mcp.tool()
def journeys_list() -> dict:
    """看看以前的旅程。每段旅程,一个世界。"""
    js = journeys.list_journeys()
    if not js:
        return {"text": "还没有旧旅程。第一次开门才算。", "data": {"journeys": []}}
    parts = []
    for j in js:
        name = j.get("place_name", "?")
        steps = j.get("steps", 0)
        parts.append(f"{name}（走了{steps}步）")
    text = f"你有 {len(js)} 段旅程。\n" + "\n".join(parts)
    return {"text": text, "data": {"journeys": js}}


@mcp.tool()
def atlas() -> dict:
    """看看你去过哪些地方。世界迷雾,一点一点亮起来。"""
    result = journeys.atlas()
    if result["places"] == 0:
        return {"text": "还没出门过。", "data": result}

    extremes = result.get("extremes", {})
    north = extremes.get("north", {}).get("name", "?")
    south = extremes.get("south", {}).get("name", "?")
    east = extremes.get("east", {}).get("name", "?")
    west = extremes.get("west", {}).get("name", "?")

    text = _rng.choice(_ATLAS_VARIANTS).format(
        places=result["places"],
        continents=result["continents"],
        north=north,
        south=south,
        east=east,
        west=west,
    )
    return {"text": text, "data": result}


@mcp.tool()
async def wait(hours: float = 1.0) -> dict:
    """原地待着,让时间流过去(0.25-12 小时)。天黑温降,城会换班。"""
    return await wait_impl(hours)


@mcp.tool()
def look(direction: str = "前") -> dict:
    """朝一个方向看。不动位置,不计时。给方位:左/右/前/后 或 N/NE/E/SE/S/SW/W/NW。"""
    return look_impl(direction)


def look_impl(direction: str) -> dict:
    """Look in a direction without moving."""
    if _state.pos is None:
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}

    # Parse direction
    _RELATIVE = {"左": -90, "右": 90, "后": 180, "前": 0, "前边": 0, "后边": 180, "左边": -90, "右边": 90}
    _ABSOLUTE = {
        "N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315,
        "北": 0, "东北": 45, "东": 90, "东南": 135, "南": 180, "西南": 225, "西": 270, "西北": 315,
    }
    d = direction.strip()
    if d in _RELATIVE:
        bearing = (_state.heading + _RELATIVE[d]) % 360
    elif d in _ABSOLUTE:
        bearing = _ABSOLUTE[d]
    else:
        bearing = _state.heading  # default: look forward

    lat, lon = _state.pos

    # Sample 3 distances: 0.5km, 2km, 10km
    samples = []
    for dist_km in (0.5, 2.0, 10.0):
        tlat, tlon = terrain.destination(lat, lon, bearing, dist_km)
        surf = terrain.surface(tlat, tlon)
        elev = terrain.elevation(tlat, tlon)
        is_water = surf in ("water_ocean", "water_fresh")
        samples.append({"dist": dist_km, "surface": surf, "elevation": elev, "water": is_water})

    # Compose description
    parts = []
    # 统一引用 describe 的权威定义，消除重复映射
    _SURFACE_ZH = describe._SURFACE_ZH

    # Near (0.5km)
    near = samples[0]
    near_zh = _SURFACE_ZH.get(near["surface"], near["surface"])
    parts.append(f"近处是{near_zh}")

    # Mid (2km)
    mid = samples[1]
    if mid["water"] and not near["water"]:
        parts.append("两公里外有水")
    elif mid["surface"] != near["surface"]:
        mid_zh = _SURFACE_ZH.get(mid["surface"], mid["surface"])
        parts.append(f"远处是{mid_zh}")

    # Far (10km)
    far = samples[2]
    if far["water"] and not mid["water"]:
        parts.append("更远的地平线是海")

    # Elevation trend
    if samples[2]["elevation"] > samples[0]["elevation"] + 200:
        parts.append("地势在升高")
    elif samples[2]["elevation"] < samples[0]["elevation"] - 200:
        parts.append("地势在走低")

    # Card 66: festival atmosphere in look
    _now_look = _state.now()
    _fest_ctx = _get_festival_context(
        _state.place_name or "", country.country_code_of(lat, lon), lat, _now_look, lon=lon,
    )
    if _fest_ctx:
        _fk = _fest_ctx.get("keywords", [])
        if _fk:
            parts.append(f"空气里有{_fk[0]}的痕迹")

    text = "，".join(parts) + "。"

    # Direction label for response
    _DIR_ZH = {0: "北", 45: "东北", 90: "东", 135: "东南", 180: "南", 225: "西南", 270: "西", 315: "西北"}
    dir_label = _DIR_ZH.get(round(bearing / 45) * 45 % 360, f"{bearing:.0f}°")

    return {
        "text": f"往{dir_label}看：{text}",
        "data": {"bearing": bearing, "samples": samples},
    }


@mcp.tool()
def say(text: str) -> dict:
    """说一句话。世界会记住。"""
    return say_impl(text)


def say_impl(text: str) -> dict:
    """Save a quote and return a light acknowledgment."""
    if _state.pos is None:
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}
    text = text.strip()
    if not text:
        return {"text": "你没说话。", "data": {"error": "empty"}}
    if len(text) > 500:
        text = text[:500]

    now = _state.now()
    sim_time = now.isoformat() if now else None
    _state.quotes.append({
        "text": text,
        "place": _state.place_name or "",
        "pos": list(_state.pos),
        "sim_time": sim_time,
    })
    # FIFO: keep last 50
    if len(_state.quotes) > 50:
        _state.quotes = _state.quotes[-50:]
    _state.save()

    # Log to journey journal
    _log_journey_event("say", text[:30])

    _ACK_VARIANTS = ["记下了。", "这句话留在这了。", "嗯。世界听到了。", "你说了。风把它带走了。"]
    ack = _rng.choice(_ACK_VARIANTS)
    return {"text": ack, "data": {"saved": True}}


@mcp.tool()
def quotes() -> dict:
    """看看本旅程说过的原话。"""
    if not _state.quotes:
        return {"text": "还没说过什么。", "data": {"quotes": []}}
    parts = []
    for q in _state.quotes:
        place = q.get("place", "")
        t = q.get("text", "")
        parts.append(f"「{t}」——{place}" if place else f"「{t}」")
    text = f"本旅程说了 {len(_state.quotes)} 句话。\n" + "\n".join(parts)
    return {"text": text, "data": {"quotes": _state.quotes}}


@mcp.tool()
def talk(question: str | None = None) -> dict:
    """和最近遇见的人搭话。不传参数=最近的人说下一句;传路怎么走=问路。"""
    return talk_impl(question)


def talk_impl(question: str | None = None) -> dict:
    """搭话。lines 轮换,第四句是记得你变体。question 含路/方向 → knows。"""
    if _state.pos is None:
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}
    if _state.last_person is None:
        return {"text": "附近没有人。", "data": {"error": "no_person"}}

    entry = _state.last_person
    place = _state.last_person_place or ""
    person = entry.get("person", "那人")

    reply = people_mod.talk(entry, _state.talk_count, question=question, rng=_rng)

    # Only advance line count if it wasn't a knows-type question
    is_knows = question and any(k in question for k in (
        "路", "怎么走", "方向", "在哪", "哪里",
        "节日", "节", "传言", "风声", "传闻", "听说",
    ))
    # ── Card 43: people notebook hook (first successful talk) ───────
    _nb_first_talk = (_state.talk_count == 0)
    if not is_knows:
        _state.talk_count += 1

    # Card 42: rumor-based letter pickup — person with knows.rumor mentioning 信
    knows = entry.get("knows", {})
    if (knows.get("type") == "rumor"
            and question and any(k in question for k in ("信", "带", "邮", "差事"))
            and not _state.errand
            and not _state.errand_letter_taken_this_journey):
        _llat, _llon = _state.pos if _state.pos else (0.0, 0.0)
        letter = errands.pick_letter(_rng, listener_lat=_llat, listener_lon=_llon)
        if letter:
            _state.errand = errands.take_letter(letter, _state.now() or datetime.now(timezone.utc))
            _state.errand_letter_taken_this_journey = True
            reply += f"\n「对了,这里有封信。{letter['sender']}托的,给{letter['recipient']}。你顺路就带一趟。」你把信接了过来。"

    # ── Card 43: people notebook hook (record on first talk) ───────
    if _nb_first_talk:
        try:
            _lat, _lon = _state.pos
            _nb_env = dict(_state.last_env or {})
            _nb_env["_dt"] = _state.now()
            notebook_mod.record_with_env("people", person, place, _nb_env, _lat)
        except Exception:
            pass

    _state.save()

    _log_journey_event("talk", f"{person}@{place}: {reply[:30]}")

    return {
        "text": reply,
        "data": {
            "person": person,
            "place": place,
            "line_index": _state.talk_count,
        },
    }


@mcp.tool()
def journal() -> dict:
    """回看本次旅程的时间线。"""
    slug = journeys.get_active_slug()
    if not slug:
        return {"text": "还没有旅程。", "data": {"entries": []}}
    log_path = journeys._JOURNEYS_DIR / f"{slug}.log.jsonl"
    if not log_path.exists():
        return {"text": "旅程日志是空的。", "data": {"entries": []}}
    try:
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        entries = [json.loads(line) for line in lines if line.strip()]
    except Exception:
        return {"text": "日志读不出来。", "data": {"entries": []}}
    if not entries:
        return {"text": "旅程日志是空的。", "data": {"entries": []}}
    parts = []
    for e in entries:
        t = e.get("t", "")
        kind = e.get("kind", "")
        summary = e.get("summary", "")
        parts.append(f"[{t}] {kind}: {summary}")
    text = "旅程时间线：\n" + "\n".join(parts)
    return {"text": text, "data": {"entries": entries}}


@mcp.tool()
def notebook(volume: str | None = None) -> dict:
    """旅行手账——五册自然志(植物/动物/电台/水文/人物)。

    不传参数: 列出所有册概况。
    传册名(flora/fauna/radio/water/people): 指定册全列。
    """
    try:
        text = notebook_mod.notebook(volume)
        return {"text": text, "data": {"volume": volume}}
    except Exception as e:
        return {"text": f"手账打不开: {e}", "data": {"error": str(e)}}


@mcp.tool()
def walk_alone() -> dict:
    """本次旅程屏蔽同游者文案。注册表保留,标记独行。下次 open_door 恢复。"""
    if not travelers_mod.is_enabled():
        return {"text": "同游者功能没有开。", "data": {"enabled": False}}
    current = travelers_mod.walk_alone_active(_state)
    if current:
        return {"text": "已经在独行了。", "data": {"alone": True}}
    travelers_mod.set_walk_alone(_state, True)
    _state.save()
    return {
        "text": "独行了。这一路上不会再看到别人的痕迹。",
        "data": {"alone": True},
    }


# ── Card 16: Blind door tools ────────────────────────────────────────

_REVEAL_VARIANTS = {
    "correct": [
        "对,就是{place}。",
        "没错,这里就是{place}。",
        "你猜对了。{place}。",
        "是{place}。你认出来了。",
    ],
    "wrong_clue": [
        "不对。给你个线索:这里在{clue}。",
        "猜错了。提示一下:这片地方属于{clue}。",
        "不是。想想看,{clue}。",
    ],
    "give_up": [
        "是{place}。你走了这么远,总算知道了。",
        "放弃也好。这里是{place}。",
        "{place}。答案一直在你脚下。",
        "这里就是{place}。你已经在这里走过了。",
    ],
    "far": [
        "是{place}。你绕了有点远。",
        "答案是{place}。线索都给过了。",
    ],
}

_BLIND_CLUE_ORDER = ["continent", "climate", "country"]


def _get_blind_clue(lat: float, lon: float, clue_level: int) -> str:
    """Return a progressive clue for blind mode."""
    if clue_level == 0:
        # Continent level
        cc = country.country_code_of(lat, lon)
        _CONTINENT_MAP = {
            "CN": "亚洲", "JP": "亚洲", "KR": "亚洲", "TH": "亚洲", "VN": "亚洲",
            "IN": "亚洲", "ID": "亚洲", "MY": "亚洲", "PH": "亚洲", "SG": "亚洲",
            "TR": "亚洲", "IL": "亚洲", "SA": "亚洲", "IR": "亚洲",
            "FR": "欧洲", "DE": "欧洲", "GB": "欧洲", "IT": "欧洲", "ES": "欧洲",
            "PT": "欧洲", "NL": "欧洲", "SE": "欧洲", "NO": "欧洲", "FI": "欧洲",
            "PL": "欧洲", "CZ": "欧洲", "GR": "欧洲", "AT": "欧洲", "CH": "欧洲",
            "RU": "欧洲", "UA": "欧洲",
            "US": "北美洲", "CA": "北美洲", "MX": "北美洲",
            "BR": "南美洲", "AR": "南美洲", "CL": "南美洲", "CO": "南美洲", "PE": "南美洲",
            "EG": "非洲", "ZA": "非洲", "NG": "非洲", "KE": "非洲", "MA": "非洲", "ET": "非洲",
            "AU": "大洋洲", "NZ": "大洋洲",
        }
        return _CONTINENT_MAP.get(cc, "一个遥远的地方")
    elif clue_level == 1:
        # Climate
        if abs(lat) < 23.5:
            return "热带"
        elif abs(lat) < 40:
            return "温带"
        elif abs(lat) < 60:
            return "寒温带"
        else:
            return "极地附近"
    else:
        # Country
        cc = country.country_code_of(lat, lon)
        return _COUNTRY_ZH.get(cc, cc or "未知国家")


@mcp.tool()
def guess(place: str) -> dict:
    """盲开模式下猜地名。猜对揭晓,猜错给线索。"""
    global _state
    if not getattr(_state, "blind", False):
        return {"text": "现在不是盲开模式。", "data": {"error": "not_blind"}}
    if _state.pos is None:
        return {"text": "还没开门呢。", "data": {"error": "not_landed"}}

    lat, lon = _state.pos
    actual_place = _state.place_name or ""
    guess_norm = place.strip().lower()
    actual_norm = actual_place.strip().lower()

    # Check if guess matches place name or country
    cc = country.country_code_of(lat, lon)
    country_zh = _COUNTRY_ZH.get(cc, "")

    # Match: exact place name, or country name if guess is country
    matched = (guess_norm == actual_norm) or (guess_norm and actual_norm and guess_norm in actual_norm)
    if not matched and country_zh:
        matched = guess_norm == country_zh.lower() or guess_norm == cc.lower()

    if matched:
        _state.blind = False
        _state.save()
        placememory.save_revealed_place(actual_place)
        text = _rng.choice(_REVEAL_VARIANTS["correct"]).format(place=actual_place)
        return {"text": text, "data": {"revealed": True, "place": actual_place, "method": "correct"}}
    else:
        _state.blind_clues = getattr(_state, "blind_clues", 0) + 1
        if _state.blind_clues >= 4:
            # Give up after 4 wrong guesses
            _state.blind = False
            _state.save()
            placememory.save_revealed_place(actual_place)
            text = _rng.choice(_REVEAL_VARIANTS["far"]).format(place=actual_place)
            return {"text": text, "data": {"revealed": True, "place": actual_place, "method": "far"}}
        else:
            clue_level = min(2, _state.blind_clues - 1)
            clue = _get_blind_clue(lat, lon, clue_level)
            _state.save()
            text = _rng.choice(_REVEAL_VARIANTS["wrong_clue"]).format(clue=clue)
            return {"text": text, "data": {"revealed": False, "clue": clue, "clue_level": clue_level}}


@mcp.tool()
def reveal() -> dict:
    """盲开模式下认输,直接揭晓地名。"""
    global _state
    if not getattr(_state, "blind", False):
        return {"text": "现在不是盲开模式。", "data": {"error": "not_blind"}}
    if _state.pos is None:
        return {"text": "还没开门呢。", "data": {"error": "not_landed"}}

    actual_place = _state.place_name or "未知之地"
    _state.blind = False
    _state.save()
    placememory.save_revealed_place(actual_place)
    text = _rng.choice(_REVEAL_VARIANTS["give_up"]).format(place=actual_place)
    return {"text": text, "data": {"revealed": True, "place": actual_place, "method": "give_up"}}


# ── Card 18: Drift card tool ────────────────────────────────────────

_drift_cache: dict | None = None


def _load_drift_cards() -> dict:
    global _drift_cache
    if _drift_cache is None:
        fp = _TIMEAXES_DATA_DIR / "drift_cards.json"
        if fp.exists():
            _drift_cache = _json.loads(fp.read_text(encoding="utf-8"))
        else:
            _drift_cache = {}
    return _drift_cache


@mcp.tool()
def drift() -> dict:
    """抽一张漂流卡,给个方向建议。脚是你的。"""
    global _state
    if _state.pos is None:
        return {"text": "还没开门呢。先开门吧。", "data": {"error": "not_landed"}}

    data = _load_drift_cards()
    biome = getattr(_state, "biome", None) or "any"
    pool = data.get(biome, data.get("any", []))
    if not pool:
        return {"text": "这里没有方向。", "data": {"error": "no_cards"}}

    # Per-journey dedup
    drift_seen = set(getattr(_state, "drift_seen", []))
    available = [c for c in pool if c["text"] not in drift_seen]
    if not available:
        # All seen in this biome, try "any"
        available = [c for c in data.get("any", []) if c["text"] not in drift_seen]
    if not available:
        return {"text": "方向都试过了。", "data": {"error": "all_seen"}}

    card = _rng.choice(available)
    _state.drift_seen = list(drift_seen | {card["text"]})
    _state.save()

    return {
        "text": card["text"],
        "data": {"action": card.get("action"), "biome": biome},
    }


def _log_journey_event(kind: str, summary: str) -> None:
    """Append an event to the current journey's log file."""
    slug = journeys.get_active_slug()
    if not slug:
        return
    log_path = journeys._JOURNEYS_DIR / f"{slug}.log.jsonl"
    journeys._ensure_dir()
    entry = {
        "t": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "pos": list(_state.pos) if _state.pos else None,
        "summary": summary,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# =====================================================================
# Entry point
# =====================================================================

def main() -> None:
    """Entry point for the ``nowhere`` console script and ``python -m``.

    Pass ``--web`` (auto-port) or ``--web PORT`` to also start the web
    observer. The URL is injected into the MCP server instructions so the
    agent learns it at handshake time and can share it with the user.
    """
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    parser = argparse.ArgumentParser(description="Nowhere MCP server")
    parser.add_argument(
        "--web",
        nargs="?",
        const=0,
        type=int,
        default=None,
        help="启动网页旁观者 (不给端口=自动选端口；--web 8080=指定端口)",
    )
    parser.add_argument("--web-only", type=int, default=None, help="Web observer port (standalone, no MCP)")
    args = parser.parse_args()

    if args.web_only is not None:
        import uvicorn
        from nowhere.web import app as web_app
        uvicorn.run(web_app, host="0.0.0.0", port=args.web_only, log_level="info")
    elif args.web is not None:
        import socket
        import sys as _sys

        import uvicorn
        from nowhere.web import app as web_app

        global _web_port, _web_url
        port = args.web
        if port == 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", 0))
                port = s.getsockname()[1]
        _web_port = port

        # Resolve the public-facing URL for remote MCP clients.
        # Priority: NOWHERE_PUBLIC_URL env > auto-detect LAN IP > localhost fallback.
        def _detect_host() -> str:
            """Return the best-guess reachable host for this machine."""
            env_url = os.environ.get("NOWHERE_PUBLIC_URL", "").strip()
            if env_url:
                return env_url.rstrip("/")
            try:
                # Connect to a public DNS to discover our LAN IP (never actually sends data).
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    return f"http://{s.getsockname()[0]}"
            except Exception:
                return "http://localhost"

        web_url = f"{_detect_host()}:{port}"
        _web_url = web_url

        # Inject the URL into the MCP server instructions so the agent
        # receives it during the initialize handshake and can tell the user.
        mcp.instructions = (
            f"网页旁观者已启动：{web_url}\n"
            "你可以告诉用户在浏览器打开这个地址，实时观看你在地球上的行走、"
            "查看地图位置和身体状态，还能在明信片下留言。"
        )
        print(f"[nowhere] web observer ready: {web_url}", file=_sys.stderr)

        async def _run_with_web() -> None:
            config = uvicorn.Config(web_app, host="0.0.0.0", port=port, log_level="warning")
            server = uvicorn.Server(config)
            web_task = asyncio.create_task(server.serve())
            web_task.add_done_callback(lambda t: t.result() if not t.cancelled() else None)
            await mcp.run_stdio_async()

        asyncio.run(_run_with_web())
    else:
        mcp.run()


if __name__ == "__main__":
    main()
