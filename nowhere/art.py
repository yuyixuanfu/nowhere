"""Art encounters -- geo-aware, mood-matched artwork from the Met Museum.

The Met has 5000+ years of art from every continent.  We bias the search
toward art that is *culturally relevant* to where the AI is standing,
then layer mood on top.  A user in Kyoto sees Japanese prints; a user
in Lagos sees West African sculpture; a user in Paris sees French painting.
"""

from __future__ import annotations

import gzip
import json as _json
import pathlib
import random
import re
from urllib.parse import quote_plus

from nowhere import providers

# ── Local art database ─────────────────────────────────────────────
_ART_DB = None
_ART_DB_PATH = pathlib.Path(__file__).resolve().parent / "data" / "art_met.json.gz"


def _load_art_db() -> dict:
    global _ART_DB
    if _ART_DB is None:
        try:
            with gzip.open(_ART_DB_PATH, "rb") as f:
                _ART_DB = _json.loads(f.read().decode("utf-8"))
        except Exception:
            _ART_DB = {"artworks": [], "by_culture": {}, "count": 0}
    return _ART_DB

SOURCE = "metmuseum"

# ── ZIM enrichment: real art interpretation ───────────────────────────
_ZIM = None
_ZIM_PATH = pathlib.Path(__file__).resolve().parent / "data" / "packs" / "wikipedia_zh_mini.zim"


def _get_zim():
    global _ZIM
    if _ZIM is None:
        try:
            from zimply.zimply import ZIMFile
            _ZIM = ZIMFile(str(_ZIM_PATH), encoding="utf-8")
        except Exception:
            _ZIM = False  # sentinel: tried and failed
    return _ZIM if _ZIM is not False else None


_TITLE_ZH: dict[str, str] = {
    "The Great Wave off Kanagawa": "神奈川冲浪里",
    "The Starry Night": "星夜",
    "Sunflowers": "向日葵",
    "The Persistence of Memory": "记忆的永恒",
    "Girl with a Pearl Earring": "戴珍珠耳环的少女",
    "The Last Supper": "最后的晚餐",
    "Mona Lisa": "蒙娜丽莎",
    "The Birth of Venus": "维纳斯的诞生",
    "The Scream": "呐喊",
    "A Sunday on La Grande Jatte": "大碗岛的星期天下午",
    "The Night Watch": "夜巡",
    "Guernica": "格尔尼卡",
    "The Kiss": "吻",
    "Water Lilies": "睡莲",
    "Impression, Sunrise": "印象·日出",
    "The Garden of Earthly Delights": "人间乐园",
    "The Creation of Adam": "创造亚当",
    "American Gothic": "美国哥特式",
    "Liberty Leading the People": "自由引导人民",
    "The Tower of Babel": "巴别塔",
    "Nighthawks": "夜游者",
    "Campbell's Soup Cans": "金宝汤罐头",
    "The Sleeping Gypsy": "沉睡的吉普赛人",
    "富春山居图": "富春山居图",
    "清明上河图": "清明上河图",
    "Fishing in Autumn on a Clear Lake": "秋江渔艇图",
    "The Fighting Temeraire": "勇猛号战舰",
    "Wheat Field with Cypresses": "麦田与柏树",
    "Café Terrace at Night": "夜间露天咖啡馆",
    "The Arnolfini Portrait": "阿尔诺芬尼夫妇像",
    "Las Meninas": "宫娥",
    "Christina's World": "克里斯蒂娜的世界",
    "The Thinker": "思想者",
    "Venus de Milo": "米洛的维纳斯",
    "Winged Victory of Samothrace": "萨莫色雷斯的胜利女神",
    "The Raft of the Medusa": "美杜莎之筏",
    "Olympia": "奥林匹亚",
    "A Bar at the Folies-Bergère": "女神游乐厅吧台",
}


def _t2s(text: str) -> str:
    """Traditional → Simplified Chinese."""
    try:
        import opencc
        converter = opencc.OpenCC("t2s")
        return converter.convert(text)
    except Exception:
        return text


def _zim_extract(title: str) -> str | None:
    """Look up an artwork in Wikipedia ZIM, return first paragraph (simplified) or None."""
    zim = _get_zim()
    if not zim:
        return None
    # Map English title to Chinese if possible
    zh_title = _TITLE_ZH.get(title, title)
    # Try exact title, then title with " (画)" suffix for disambiguation
    for candidate in [zh_title, f"{zh_title} (画)", f"{zh_title} (绘画)", title]:
        try:
            art = zim.get_article_by_url("C", candidate)
            if art and art.data:
                html = art.data.decode("utf-8", errors="replace")
                m = re.search(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
                if m:
                    text = m.group(1)
                    text = re.sub(r"<sup[^>]*>.*?</sup>", "", text, flags=re.DOTALL)
                    text = re.sub(r"<[^>]+>", "", text)
                    text = re.sub(r"\s+", " ", text).strip()
                    if len(text) > 20:
                        return _t2s(text[:300])
        except Exception:
            continue
    return None

# ── Geo → culture search terms ──────────────────────────────────────
# Map lat/lon bands to Met search keywords.  Crude but effective:
# the Met's search indexes culture/department/tags, so "Japanese"
# reliably surfaces Japanese art.

_GEO_CULTURE: list[tuple[float, float, float, float, str]] = [
    # (lat_min, lat_max, lon_min, lon_max, search_keyword)
    # Central Asia / Mongolia (BEFORE Chinese to avoid overlap)
    (40, 55, 87, 120, "Central Asian"),
    (35, 55, 50, 90, "Islamic"),
    # East Asia
    (20, 50, 100, 145, "Japanese"),
    (33, 43, 124, 132, "Korean"),
    (18, 55, 73, 135, "Chinese"),
    # South / Southeast Asia
    (5, 38, 60, 100, "Indian"),
    (-10, 25, 90, 155, "Southeast Asian"),
    # Middle East / North Africa
    (10, 45, 25, 65, "Islamic"),
    (20, 40, -15, 55, "Egyptian"),
    # Sub-Saharan Africa
    (-35, 20, -20, 55, "African"),
    # Europe
    (35, 72, -15, 40, "European"),
    (35, 60, -10, 3, "Spanish"),
    (36, 48, 6, 18, "Italian"),
    (42, 52, -6, 10, "French"),
    (47, 60, 5, 30, "German"),
    (50, 62, -10, 2, "British"),
    (55, 85, 5, 35, "Scandinavian"),
    # Americas
    (10, 35, -130, -60, "American"),
    (-55, 15, -85, -35, "Latin American"),
    (15, 33, -120, -85, "Pre-Columbian"),
    # Oceania
    (-50, -5, 110, 180, "Oceanic"),
]


def _geo_culture(lat: float, lon: float) -> str | None:
    """Return a Met search keyword for the region, or None."""
    for lat_min, lat_max, lon_min, lon_max, kw in _GEO_CULTURE:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return kw
    return None


# ── Mood search terms ───────────────────────────────────────────────

_MOOD_SEARCH: dict[str, str] = {
    "rain": "rain",
    "night": "night",
    "snow": "snow winter",
    "dawn": "dawn sunrise morning",
    "storm": "storm tempest",
    "calm": "calm peace serene",
    "sun": "sun sunshine",
    "fog": "fog mist",
    "wind": "wind",
}

_MOOD_WHY: dict[str, str] = {
    "rain": "雨天看这幅画，刚好",
    "night": "夜色正浓，这件作品映着此刻",
    "snow": "雪天的氛围，和这幅画相通",
    "dawn": "黎明时分遇见它，像是巧合",
    "storm": "风暴将至，这幅画也躁动",
    "calm": "此刻平静，这件作品也安静",
    "sun": "阳光下的遇见",
    "fog": "雾气里看到这幅画，别有意境",
    "wind": "风里带着和这幅画一样的气息",
}


def _mood_why(mood: str) -> str:
    return _MOOD_WHY.get(mood, "此刻应景")


# ── Local art match ─────────────────────────────────────────────────


def _local_art_match(lat: float, lon: float, mood: str, rng: random.Random) -> dict | None:
    """Find artwork from local database matching geo region."""
    db = _load_art_db()
    artworks = db.get("artworks", [])
    if not artworks:
        return None

    # Get geo culture keyword
    culture = _geo_culture(lat, lon)

    # Try culture-specific first
    candidates = []
    if culture:
        culture_lower = culture.lower()
        indices = db.get("by_culture", {}).get(culture_lower, [])
        candidates = [artworks[i] for i in indices if i < len(artworks)]

    # If no culture match, use all
    if not candidates:
        candidates = artworks

    # Pick random from top 50
    pool = candidates[:50]
    rng.shuffle(pool)
    for art in pool[:5]:
        if art.get("image") and art.get("title"):
            return {
                "title": art["title"],
                "artist": art.get("artist", "佚名"),
                "artist_bio": art.get("bio", ""),
                "year": art.get("year", ""),
                "image_url": art.get("image", ""),
                "culture": art.get("culture", ""),
                "medium": art.get("class", ""),
                "classification": art.get("class", ""),
                "department": art.get("dept", ""),
                "tags": [],
                "zim_extract": None,  # will be filled by caller
                "why": _mood_why(mood),
            }
    return None


# ── Main match function ─────────────────────────────────────────────

async def match(lat: float, lon: float, mood: str, rng: random.Random | None = None) -> dict | None:
    """Return a geo-aware, mood-matched artwork, or *None*.

    Strategy:
      1. Try local database first (fast, no network)
      2. Fall back to Met API (culture + mood, then mood only)
    """
    if rng is None:
        rng = random.Random()
    if not mood or mood.lower() in ("none", ""):
        mood = "calm"

    # ── 1. Try local database first ─────────────────────────────────
    result = _local_art_match(lat, lon, mood, rng)
    if result:
        # Try ZIM enrichment
        title = result.get("title", "")
        if title:
            zim_text = _zim_extract(title)
            if zim_text:
                result["zim_extract"] = zim_text
        return result

    # ── 2. Fall back to Met API ─────────────────────────────────────
    mood_term = _MOOD_SEARCH.get(mood, mood)
    culture = _geo_culture(lat, lon)

    # Try culture + mood first
    if culture:
        search_term = f"{culture} {mood_term}"
        api_result = await _search_and_pick(search_term, mood, rng)
        if api_result:
            return api_result

    # Fall back to mood only
    api_result = await _search_and_pick(mood_term, mood, rng)
    if api_result:
        return api_result

    # Last resort: generic "art"
    return await _search_and_pick("painting", mood, rng)


async def _search_and_pick(search_term: str, mood: str, rng: random.Random) -> dict | None:
    """Search Met API and return a random artwork, or None."""
    search_url = (
        "https://collectionapi.metmuseum.org/public/collection/v1/search"
        f"?hasImages=true&q={quote_plus(search_term)}"
    )
    search_data = await providers.fetch_json(
        search_url, source=SOURCE, cache_ttl=600, timeout=5.0,
    )
    if not search_data or not search_data.get("objectIDs"):
        return None

    # Pick from top 20 for variety
    object_ids: list[int] = search_data["objectIDs"][:20]
    # Shuffle and try up to 5 (some entries have no image)
    rng.shuffle(object_ids)
    for oid in object_ids[:5]:
        obj_url = (
            "https://collectionapi.metmuseum.org/public/collection/v1"
            f"/objects/{oid}"
        )
        obj_data = await providers.fetch_json(
            obj_url, source=SOURCE, cache_ttl=600, timeout=5.0,
        )
        if not obj_data:
            continue
        image_url = obj_data.get("primaryImage") or ""
        title = obj_data.get("title", "")
        if not image_url or not title or title.lower() == "none":
            continue

        tags_raw = obj_data.get("tags") or []
        tags = [
            t.get("name", "")
            for t in tags_raw
            if isinstance(t, dict) and t.get("name")
        ]

        # Try ZIM enrichment: real art interpretation
        zim_text = _zim_extract(title)

        return {
            "title": title,
            "artist": obj_data.get("artistDisplayName", "") or "佚名",
            "artist_bio": obj_data.get("artistDisplayBio", ""),
            "year": obj_data.get("objectDate", ""),
            "image_url": image_url,
            "culture": obj_data.get("culture", ""),
            "medium": obj_data.get("medium", ""),
            "classification": obj_data.get("classification", ""),
            "department": obj_data.get("department", ""),
            "tags": tags[:5],
            "zim_extract": zim_text,
            "why": _mood_why(mood),
        }
    return None
