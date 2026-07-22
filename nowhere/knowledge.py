"""Knowledge encounters -- local facts via offline Wikipedia ZIM file."""

from __future__ import annotations

import json
import logging
import pathlib
import re
import urllib.parse

logger = logging.getLogger(__name__)

_ZIM_PATH = pathlib.Path(__file__).resolve().parent / "data" / "packs" / "wikipedia_zh_mini.zim"
_NAMESPACE = "C"  # articles namespace in this ZIM
_WIKI_BASE = "https://zh.wikipedia.org/wiki/"
_MAX_EXTRACT = 500  # chars

_zim = None  # lazy singleton

# --- Local knowledge base -------------------------------------------------
_DATA = pathlib.Path(__file__).resolve().parent / "data"

_KB_FILES = [
    "knowledge.json",
]

_local_kb: dict[str, dict] | None = None


def _load_local_kb() -> dict[str, dict]:
    """Load and merge all local knowledge base JSON files (cached)."""
    global _local_kb
    if _local_kb is None:
        _local_kb = {}
        for fname in _KB_FILES:
            fp = _DATA / fname
            if fp.exists():
                data = json.loads(fp.read_text(encoding="utf-8"))
                _local_kb.update(data)
    return _local_kb


def _format_kb_entry(name: str, entry: dict) -> dict:
    """Format a local KB entry as a knowledge result."""
    parts = []
    for key in ["一句话", "特色", "语言", "首都", "海拔"]:
        if key in entry:
            parts.append(f"{key}：{entry[key]}")
    extract = "。".join(parts) if parts else ""
    return {
        "title": name,
        "extract": extract,
        "url": "",
        "source": "local_kb",
    }

# Common short queries → known article titles that should exist in the ZIM
_COMMON_TOPICS: dict[str, str] = {
    "火山": "火山",
    "地震": "地震",
    "河流": "河",
    "山": "山",
    "海": "海",
    "沙漠": "沙漠",
    "森林": "森林",
    "冰川": "冰川",
    "雨林": "热带雨林",
    "草原": "草原",
    "湖": "湖",
    "瀑布": "瀑布",
    "峡谷": "峡谷",
    "岛屿": "岛",
    "半岛": "半岛",
}


def _get_zim():
    """Open the ZIM file once and cache."""
    global _zim
    if _zim is not None:
        return _zim
    if not _ZIM_PATH.exists():
        logger.warning("ZIM file not found: %s", _ZIM_PATH)
        return None
    try:
        # Suppress gevent's noisy atexit KeyError from zimply's monkey-patching
        import atexit
        import threading
        _orig_excepthook = threading.excepthook
        def _silent_thread_excepthook(args):
            if isinstance(args.exc_value, KeyError):
                return  # suppress gevent cleanup KeyError
            _orig_excepthook(args)
        threading.excepthook = _silent_thread_excepthook

        from zimply.zimply import ZIMFile
        _zim = ZIMFile(str(_ZIM_PATH), encoding="utf-8")
        return _zim
    except Exception as exc:
        logger.warning("Failed to open ZIM file: %s", exc)
        return None


def _strip_html(html: str) -> str:
    """Extract plain text from the first non-empty <p> block of a Wikipedia HTML article."""
    # Try each <p>...</p> block until we find one with real text
    for m in re.finditer(r"<p[^>]*>(.*?)</p>", html, re.DOTALL):
        text = m.group(1)
        # Remove <sup>...</sup> (references)
        text = re.sub(r"<sup[^>]*>.*?</sup>", "", text, flags=re.DOTALL)
        # Remove all remaining tags
        text = re.sub(r"<[^>]+>", "", text)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text
    return ""


def _article_title_from_html(html: str) -> str | None:
    """Extract the <title> content from the HTML."""
    m = re.search(r"<title>([^<]+)</title>", html)
    return m.group(1).strip() if m else None


async def about(lat: float, lon: float, topic: str) -> dict | None:
    """Return a Wikipedia article from the offline ZIM, or *None*.

    Parameters
    ----------
    topic:
        Search filter.  If non-empty, look up the article by that title.
        If empty, try to find the nearest named place from *lat/lon*.
    """
    title = topic.strip() if topic else ""

    # --- 1. Try local knowledge base first ---
    kb = _load_local_kb()

    # Exact match on topic
    if title and title in kb:
        return _format_kb_entry(title, kb[title])

    # Fuzzy match (contains)
    if title:
        for name, entry in kb.items():
            if title in name or name in title:
                return _format_kb_entry(name, entry)

    # Try place name from coordinates
    place_name = await _resolve_place_name(lat, lon)
    if place_name and place_name in kb:
        return _format_kb_entry(place_name, kb[place_name])

    # --- 2. Fall back to ZIM lookup ---
    if not title:
        if not place_name:
            return None
        title = place_name

    zim = _get_zim()
    if zim is None:
        return None

    # Strategy 1: Try direct lookup
    result = _try_zim_lookup(zim, title)
    if result is not None:
        return result

    # Strategy 2: Try URL-encoded title (ZIM may store URLs encoded)
    encoded = urllib.parse.quote(title, safe="")
    if encoded != title:
        result = _try_zim_lookup(zim, encoded)
        if result is not None:
            return result

    # Strategy 3: Try with underscores instead of spaces
    if " " in title:
        result = _try_zim_lookup(zim, title.replace(" ", "_"))
        if result is not None:
            return result

    # Strategy 4: Check _COMMON_TOPICS for known article titles
    if title in _COMMON_TOPICS:
        mapped = _COMMON_TOPICS[title]
        if mapped != title:
            result = _try_zim_lookup(zim, mapped)
            if result is not None:
                return result

    # Strategy 5: Try common Wikipedia disambiguation / related suffixes
    for suffix in (" (地理)", " (地质学)", " (消歧義)", "地貌", "地形"):
        result = _try_zim_lookup(zim, title + suffix)
        if result is not None:
            return result

    # Strategy 6: combine with nearest place name (reuse already-resolved name)
    place = place_name
    if place and place != title:
        for combo in (f"{place} {title}", f"{title}_{place}"):
            result = _try_zim_lookup(zim, combo)
            if result is not None:
                return result

    # Strategy 7: Try ZIM index search for titles containing the query
    if len(title) >= 2:
        try:
            matches = zim.suggest(title)
            if matches:
                # suggest returns a list of title strings
                for match_title in matches[:5]:
                    if match_title and match_title != title:
                        result = _try_zim_lookup(zim, match_title)
                        if result is not None:
                            return result
        except Exception as exc:
            logger.debug("ZIM suggest failed for %r: %s", title, exc)

    # Strategy 8: Try fulltext search if available
    if len(title) >= 2:
        try:
            search_results = zim.search(title, 5)
            if search_results:
                for entry in search_results:
                    entry_title = entry if isinstance(entry, str) else getattr(entry, "title", None) or getattr(entry, "url", "")
                    if entry_title and entry_title != title:
                        result = _try_zim_lookup(zim, entry_title)
                        if result is not None:
                            return result
        except Exception as exc:
            logger.debug("ZIM search failed for %r: %s", title, exc)

    return None


def _try_zim_lookup(zim, title: str) -> dict | None:
    """Attempt a single ZIM article lookup.  Returns result dict or None."""
    art = zim.get_article_by_url(_NAMESPACE, title)
    if art is None or art.data is None:
        return None

    html = art.data.decode("utf-8", errors="replace") if isinstance(art.data, bytes) else art.data

    display_title = _article_title_from_html(html) or title
    extract = _strip_html(html)
    if not extract:
        return None

    if len(extract) > _MAX_EXTRACT:
        # Cut at sentence boundary if possible
        cut = extract[:_MAX_EXTRACT]
        last_period = max(cut.rfind("。"), cut.rfind("．"), cut.rfind(". "), cut.rfind("."))
        extract = cut[: last_period + 1] if last_period > 100 else cut + "..."

    url = _WIKI_BASE + urllib.parse.quote(title, safe="")

    return {
        "title": display_title,
        "extract": extract,
        "url": url,
    }


async def _resolve_place_name(lat: float, lon: float) -> str:
    """Get the nearest named place for given coordinates."""
    try:
        from nowhere import places
        nearby = places.nearby(lat, lon, radius_km=20, limit=1)
        if nearby:
            return nearby[0]["name"]
    except Exception as exc:
        logger.debug("places.nearby failed: %s", exc)
    return ""
