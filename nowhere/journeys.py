"""Multi-journey management for nowhere.

Stores each journey as a separate JSON file under ~/.nowhere/journeys/.
An index.json tracks the active journey and metadata for all journeys.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from datetime import datetime, timezone
from typing import Any

from nowhere.state import WorldState

_CONTINENT_MAP: dict[str, str] = {
    # Asia
    "CN": "亚洲", "JP": "亚洲", "KR": "亚洲", "KP": "亚洲", "MN": "亚洲",
    "IN": "亚洲", "TH": "亚洲", "VN": "亚洲", "MY": "亚洲", "SG": "亚洲",
    "ID": "亚洲", "PH": "亚洲", "MM": "亚洲", "KH": "亚洲", "LA": "亚洲",
    "NP": "亚洲", "BD": "亚洲", "LK": "亚洲", "PK": "亚洲", "AF": "亚洲",
    "IR": "亚洲", "IQ": "亚洲", "TR": "亚洲", "SA": "亚洲", "AE": "亚洲",
    "IL": "亚洲", "JO": "亚洲", "LB": "亚洲", "SY": "亚洲",
    "KZ": "亚洲", "UZ": "亚洲", "TM": "亚洲", "KG": "亚洲", "TJ": "亚洲",
    "GE": "亚洲", "AM": "亚洲", "AZ": "亚洲",
    # Europe
    "GB": "欧洲", "FR": "欧洲", "DE": "欧洲", "IT": "欧洲", "ES": "欧洲",
    "PT": "欧洲", "NL": "欧洲", "BE": "欧洲", "CH": "欧洲", "AT": "欧洲",
    "SE": "欧洲", "NO": "欧洲", "FI": "欧洲", "DK": "欧洲", "IS": "欧洲",
    "PL": "欧洲", "CZ": "欧洲", "SK": "欧洲", "HU": "欧洲", "RO": "欧洲",
    "BG": "欧洲", "GR": "欧洲", "HR": "欧洲", "RS": "欧洲", "UA": "欧洲",
    "BY": "欧洲", "LT": "欧洲", "LV": "欧洲", "EE": "欧洲",
    "SI": "欧洲", "BA": "欧洲", "ME": "欧洲", "MK": "欧洲", "AL": "欧洲",
    "MD": "欧洲", "IE": "欧洲", "LU": "欧洲",
    "RU": "欧洲",
    # North America
    "US": "北美洲", "CA": "北美洲", "MX": "北美洲", "CU": "北美洲",
    "JM": "北美洲", "HT": "北美洲", "DO": "北美洲", "GT": "北美洲",
    "HN": "北美洲", "SV": "北美洲", "NI": "北美洲", "CR": "北美洲", "PA": "北美洲",
    "BS": "北美洲", "BZ": "北美洲", "GL": "北美洲",
    # South America
    "BR": "南美洲", "AR": "南美洲", "CL": "南美洲", "PE": "南美洲",
    "CO": "南美洲", "VE": "南美洲", "EC": "南美洲", "BO": "南美洲",
    "PY": "南美洲", "UY": "南美洲", "GY": "南美洲", "SR": "南美洲",
    # Africa
    "EG": "非洲", "ZA": "非洲", "NG": "非洲", "KE": "非洲", "ET": "非洲",
    "MA": "非洲", "TN": "非洲", "DZ": "非洲", "TZ": "非洲", "UG": "非洲",
    "GH": "非洲", "SN": "非洲", "ML": "非洲", "NE": "非洲", "TD": "非洲",
    "CM": "非洲", "CD": "非洲", "CG": "非洲", "AO": "非洲", "ZM": "非洲",
    "ZW": "非洲", "MZ": "非洲", "MG": "非洲", "NA": "非洲", "BW": "非洲",
    "SD": "非洲", "LY": "非洲", "SO": "非洲",
    # Oceania
    "AU": "大洋洲", "NZ": "大洋洲", "FJ": "大洋洲", "PG": "大洋洲",
    "SB": "大洋洲", "VU": "大洋洲", "WS": "大洋洲", "TO": "大洋洲",
}

_JOURNEYS_DIR = pathlib.Path(
    os.environ.get("NOWHERE_HOME") or str(pathlib.Path.home() / ".nowhere")
) / "journeys"
_INDEX_FILE = _JOURNEYS_DIR / "index.json"


def _slug(place_name: str) -> str:
    """Normalize place name to a filesystem-safe slug."""
    s = place_name.strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^\w一-鿿-]", "", s)
    return s or "unknown"


def _ensure_dir() -> None:
    _JOURNEYS_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> dict:
    """Load or initialize the journey index."""
    if _INDEX_FILE.exists():
        try:
            return json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"active": None, "journeys": []}


def _save_index(index: dict) -> None:
    """Persist the journey index."""
    _ensure_dir()
    _INDEX_FILE.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _journey_path(slug: str) -> pathlib.Path:
    return _JOURNEYS_DIR / f"{slug}.json"


def save_current(state: WorldState) -> None:
    """Save the current state as a journey file and update the index."""
    _ensure_dir()
    place = state.place_name or "unknown"
    slug = _slug(place)
    path = _journey_path(slug)

    # Save state
    data = state.to_dict()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Update index
    index = _load_index()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Find existing entry
    existing = None
    for j in index["journeys"]:
        if j["slug"] == slug:
            existing = j
            break

    if existing:
        existing["last_active"] = now_iso
        existing["departed_at"] = now_iso
        existing["steps"] = len(state.path)
        existing["last_text"] = (state.last_text or "")[:50]
    else:
        index["journeys"].append({
            "slug": slug,
            "place_name": place,
            "landed_at": state.landed_at.isoformat() if state.landed_at else now_iso,
            "last_active": now_iso,
            "departed_at": now_iso,
            "steps": len(state.path),
            "last_text": (state.last_text or "")[:50],
        })

    index["active"] = slug
    _save_index(index)


def list_journeys() -> list[dict]:
    """List all saved journeys with metadata."""
    index = _load_index()
    return index.get("journeys", [])


def get_active_slug() -> str | None:
    """Return the active journey slug, or None."""
    return _load_index().get("active")


def switch(slug_or_place: str) -> WorldState | None:
    """Switch to a journey by slug or exact place name. Returns WorldState or None.

    Card 68: only exact match — no substring matching.
    "上海" must NOT match "长江上海段".
    """
    index = _load_index()
    target = _slug(slug_or_place)

    # Try exact slug match
    for j in index["journeys"]:
        if j["slug"] == target:
            return _load_journey(j["slug"], index)

    # Try exact place_name match (case-insensitive)
    query_lower = slug_or_place.strip().lower()
    for j in index["journeys"]:
        if j.get("place_name", "").strip().lower() == query_lower:
            return _load_journey(j["slug"], index)

    return None


def get_journey_meta(slug_or_place: str) -> dict | None:
    """Return index metadata for a journey, or None if not found."""
    index = _load_index()
    target = _slug(slug_or_place)

    # Try exact slug match first
    for j in index["journeys"]:
        if j["slug"] == target:
            return j

    # Try exact match (case-insensitive)
    slug_or_lower = slug_or_place.strip().lower()
    for j in index["journeys"]:
        if j.get("place_name", "").strip().lower() == slug_or_lower:
            return j

    return None


def _load_journey(slug: str, index: dict) -> WorldState | None:
    """Load a journey file and set it as active.

    Validates that the loaded state's place_name matches the index entry
    to prevent cross-contamination (e.g. file overwritten by a different journey).
    """
    path = _journey_path(slug)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        state = WorldState.from_dict(data)
        # Validate: loaded state's place must match index entry
        expected_place = None
        for j in index.get("journeys", []):
            if j["slug"] == slug:
                expected_place = j.get("place_name", "")
                break
        if expected_place and state.place_name:
            if _slug(state.place_name) != _slug(expected_place):
                return None  # cross-contamination detected
        index["active"] = slug
        _save_index(index)
        return state
    except Exception:
        return None


def delete(slug: str) -> bool:
    """Delete a journey file. Returns True if deleted."""
    path = _journey_path(slug)
    if path.exists():
        path.unlink()
    index = _load_index()
    index["journeys"] = [j for j in index["journeys"] if j["slug"] != slug]
    if index["active"] == slug:
        index["active"] = None
    _save_index(index)
    return True


def atlas() -> dict:
    """聚合全部旅程: 地方数、大洲数、极端方向。

    Returns dict with keys: places, continents, extremes.
    """
    from nowhere.country import country_code_of

    index = _load_index()
    journeys_list = index.get("journeys", [])

    if not journeys_list:
        return {"places": 0, "continents": 0, "extremes": {}}

    places: list[dict] = []
    for j in journeys_list:
        slug = j.get("slug")
        place_name = j.get("place_name", "")
        path = _journey_path(slug)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                pos = data.get("pos")
                if pos and len(pos) >= 2:
                    places.append({
                        "name": place_name,
                        "lat": pos[0],
                        "lon": pos[1],
                    })
            except Exception:
                continue

    if not places:
        return {"places": 0, "continents": 0, "extremes": {}}

    # Continent count
    continents: set[str] = set()
    for p in places:
        cc = country_code_of(p["lat"], p["lon"])
        if cc:
            cont = _CONTINENT_MAP.get(cc, "")
            if cont:
                continents.add(cont)

    # Extremes
    extremes: dict[str, dict] = {}
    extremes["north"] = max(places, key=lambda p: p["lat"])
    extremes["south"] = min(places, key=lambda p: p["lat"])
    extremes["east"] = max(places, key=lambda p: p["lon"])
    extremes["west"] = min(places, key=lambda p: p["lon"])

    return {
        "places": len(places),
        "continents": len(continents),
        "extremes": extremes,
    }
