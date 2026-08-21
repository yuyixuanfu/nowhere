"""Knowledge encounters -- local facts via pre-built knowledge base.

All heavy lifting (Wikipedia extraction) is done offline by
``tools/build_ask_kb.py`` → ``data/ask_kb.json``.  Runtime code only reads
JSON — no large file parsing, no third-party parser dependencies.
"""

from __future__ import annotations

import json
import logging
import pathlib
import random as _random
import re

logger = logging.getLogger(__name__)

# --- Local knowledge base -------------------------------------------------
_DATA = pathlib.Path(__file__).resolve().parent / "data"

_KB_FILES = [
    "knowledge.json",
    "ask_kb.json",
]

_local_kb: dict[str, dict] | None = None
# Inverted index: first character → list of KB keys starting with that char
_kb_char_index: dict[str, list[str]] | None = None
# Label index: loaded from ask_labels.json
_labels: dict[str, list[str]] | None = None
# Topic word → label mapping (卡88)
_TOPIC_LABELS: dict[str, list[str]] = {
    "历史": ["历史政体", "事件"],
    "好吃的": ["饮食"],
    "美食": ["饮食"],
    "风俗": ["风俗", "事件"],
    "习俗": ["风俗"],
    "节日": ["节日"],
    "建筑": ["建筑", "地标"],
    "动物": ["动物"],
    "植物": ["自然"],
    "风景": ["自然", "地标"],
    "音乐": ["音乐"],
    "运动": ["体育"],
    "体育": ["体育"],
    "科技": ["科技"],
    "名人": ["人物"],
    "皇帝": ["人物", "历史政体"],
    "王朝": ["历史政体"],
    "服装": ["服装"],
    "食物": ["饮食"],
    "菜系": ["饮食"],
    "小吃": ["饮食"],
    "水果": ["饮食"],
    "艺术": ["艺术"],
    "文化": ["风俗", "艺术"],
    "宗教": ["风俗"],
    "语言": ["语言"],
    "职业": ["职业"],
    "学校": ["学科"],
    "大学": ["学科"],
}


def _load_local_kb() -> dict[str, dict]:
    """Load and merge all local knowledge base JSON files (cached).

    ``knowledge.json`` has structured entries (dicts with 一句话 / card / …).
    ``ask_kb.json`` has flat ``{name: extract_text}`` entries produced by the
    offline builder.  They are normalised to the same ``{title, extract, …}``
    shape so the rest of the code can treat them uniformly.
    """
    global _local_kb, _kb_char_index, _labels
    if _local_kb is not None:
        return _local_kb

    _local_kb = {}
    for fname in _KB_FILES:
        fp = _DATA / fname
        if not fp.exists():
            continue
        data = json.loads(fp.read_text(encoding="utf-8"))
        _local_kb.update(data)

    # Build first-character inverted index for substring lookups
    idx: dict[str, list[str]] = {}
    for name in _local_kb:
        if name:
            idx.setdefault(name[0], []).append(name)
    _kb_char_index = idx

    # Load labels (卡88)
    labels_fp = _DATA / "ask_labels.json"
    if labels_fp.exists():
        _labels = json.loads(labels_fp.read_text(encoding="utf-8"))

    return _local_kb


def _format_kb_entry(name: str, entry: dict | str) -> dict:
    """Format a local KB entry as a knowledge result.

    Two shapes live in the merged KB:

    * Structured (from ``knowledge.json``):  ``{一句话, 特色, 语言, 首都, 海拔, 位置, 冷知识, 别名, 货币, card, ...}``
    * Flat (from ``ask_kb.json``):  plain ``str`` (the extract text)

    Both are normalised to ``{title, extract, url, source}``.
    """
    # --- flat string entry (from ask_kb.json) ---
    if isinstance(entry, str):
        return {
            "title": name,
            "extract": entry,
            "url": "",
            "source": "ask_kb",
        }

    # --- structured dict entry (from knowledge.json) ---
    parts: list[str] = []
    for key in ["一句话", "特色", "语言", "首都"]:
        if key in entry and isinstance(entry[key], str):
            parts.append(entry[key])
    alt = entry.get("海拔")
    if isinstance(alt, str) and alt:
        m = re.match(r"([\d.]+)\s*m", alt.strip())
        parts.append(f"海拔约 {m.group(1)} 米" if m else alt)
    # 位置 / 冷知识 / 别名 / 货币 (S2 接线)
    loc = entry.get("位置")
    if isinstance(loc, str) and loc:
        parts.append(loc)
    trivia = entry.get("冷知识")
    if isinstance(trivia, str) and trivia:
        parts.append(trivia)
    alias = entry.get("别名")
    if isinstance(alias, str) and alias:
        parts.append(f"又称{alias}")
    currency = entry.get("货币")
    if isinstance(currency, str) and currency:
        parts.append(f"当地用{currency}")
    cards = entry.get("card")
    if isinstance(cards, list):
        for c in cards:
            if isinstance(c, dict):
                content = c.get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
    extract = "。".join(p.rstrip("。") for p in parts if p)
    return {
        "title": name,
        "extract": extract,
        "url": "",
        "source": "local_kb",
    }


async def about(lat: float, lon: float, topic: str) -> dict | None:
    """Return a knowledge result from the local KB, or *None*.

    Parameters
    ----------
    topic:
        Search filter.  If non-empty, look up the article by that title.
        If empty, try to find the nearest named place from *lat/lon*.
    """
    title = topic.strip() if topic else ""
    place_name = ""

    kb = _load_local_kb()

    # ── 1. Exact match ──
    if title and title in kb:
        return _format_kb_entry(title, kb[title])

    # ── 2. Bidirectional substring (卡88) ──
    if title:
        idx = _kb_char_index or {}
        # Forward: "故宫有什么" contains "故宫" → match
        for ch in set(title):
            for name in idx.get(ch, []):
                if name in title:
                    return _format_kb_entry(name, kb[name])
        # Backward: "故宫" is substring of "故宫博物院"
        for ch in set(title):
            for name in idx.get(ch, []):
                if title in name:
                    return _format_kb_entry(name, kb[name])

    # ── 3. Entity extraction: find KB keys mentioned in topic (卡88) ──
    if title:
        # Sort by length descending to match longest key first
        for name in sorted(kb.keys(), key=len, reverse=True):
            if len(name) >= 2 and name in title:
                return _format_kb_entry(name, kb[name])

    # ── 4. Topic word mapping (卡88) ──
    if title:
        place_name = await _resolve_place_name(lat, lon)
        for topic_word, target_labels in _TOPIC_LABELS.items():
            if topic_word in title:
                # Search for entries with matching labels near current place
                if _labels and place_name:
                    for name, tags in _labels.items():
                        if any(t in tags for t in target_labels):
                            # Check if this entry is related to current place
                            if place_name in name or name in place_name:
                                return _format_kb_entry(name, kb[name])
                    # If no place-specific match, return any matching label
                    for name, tags in _labels.items():
                        if any(t in tags for t in target_labels):
                            return _format_kb_entry(name, kb[name])
                break

    # ── 5. Label fallback (卡88) ──
    if title and _labels:
        place_name = place_name or await _resolve_place_name(lat, lon)
        if place_name:
            # Find entries related to current place with any label
            for name, tags in _labels.items():
                if place_name in name and tags:
                    return _format_kb_entry(name, kb[name])

    # ── 6. Coordinate fallback (no topic) ──
    if not title:
        place_name = place_name or await _resolve_place_name(lat, lon)
        if place_name and place_name in kb:
            return _format_kb_entry(place_name, kb[place_name])

    return None


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


# ── Voice layer: t2s + strip wiki tone + truncate + distancing ───────

_MAX_VOICE_LEN = 150

_WIKI_OPENING_RE = re.compile(
    r'^[一-鿿·]{1,20}(?:是|位于|坐落于|地处|属于|为)'
)

_DISTANCING_LINES: list[str] = [
    "这是书上说的。",
    "书里这么写的,对不对你到了再看。",
    "文字是这么记的。",
]


def _t2s(text: str) -> str:
    """Traditional to Simplified Chinese (opencc t2s)."""
    try:
        import opencc
        converter = opencc.OpenCC("t2s")
        return converter.convert(text)
    except Exception:
        return text


def _strip_wiki_opening(text: str) -> str:
    """Replace encyclopedia-style opening with natural entry."""
    m = _WIKI_OPENING_RE.match(text)
    if m:
        text = text[m.end():]
        text = text.lstrip('，,。 ')
    return text


def _truncate_at_boundary(text: str, max_len: int = _MAX_VOICE_LEN) -> str:
    """Cut to <=max_len chars, preferring sentence boundaries."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    for sep in ('。', '！', '？', '；', '.', '!', '?'):
        idx = cut.rfind(sep)
        if idx > 50:
            return cut[:idx + 1]
    return cut + "……"


def voice_layer(text: str, rng: _random.Random | None = None) -> str:
    """Voice processing: t2s, strip wiki tone, truncate, add distancing line.

    Call this on every KB extract before returning to the user.
    """
    if not text:
        return text
    text = _t2s(text)
    text = _strip_wiki_opening(text)
    text = _truncate_at_boundary(text)
    if rng is None:
        rng = _random.Random()
    text += rng.choice(_DISTANCING_LINES)
    return text


def has_knowledge(topic: str) -> bool:
    """Quick sync check: does the knowledge base have content for *topic*?

    Used by walk_impl to decide whether to hint 'ask 能问出更多'.
    Uses a first-character inverted index to avoid scanning all KB keys.
    """
    kb = _load_local_kb()
    if not topic:
        return False
    if topic in kb:
        return True
    # Narrow candidates via inverted index: only check keys whose first
    # character matches the query's first character (or second, if the
    # query starts with a common prefix like a space).
    idx = _kb_char_index or {}
    candidates = idx.get(topic[0], [])
    for name in candidates:
        if topic in name:
            return True
    return False
