"""差事发生器——邮差、寻宝链、节日追逐。

设计公理:事本身就是报酬。无奖励、无完成音效、无进度条。

三种差事:
  1. 邮差(letter): 送一封信到特征描述的地方。
  2. 寻宝链(chain): bury note 含"下一个"→链式接力,最多3站。
  3. 节日追逐(festival): 800km 内有节日开幕→风声,不接取。
"""

from __future__ import annotations

import json
import math
import pathlib
import random
from datetime import datetime, timezone

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
_LETTERS_FILE = _DATA_DIR / "errands_letters.json"

_letters_cache: list[dict] | None = None


def _load_letters() -> list[dict]:
    """Load errands_letters.json once and cache."""
    global _letters_cache
    if _letters_cache is not None:
        return _letters_cache
    if not _LETTERS_FILE.exists():
        _letters_cache = []
        return _letters_cache
    _letters_cache = json.loads(_LETTERS_FILE.read_text(encoding="utf-8"))
    return _letters_cache


def pick_letter(
    rng: random.Random,
    listener_lat: float = 0.0,
    listener_lon: float = 0.0,
) -> dict | None:
    """Pick a random letter from the pool. Returns letter dict or None.

    Card 68: letters with destination coordinates are distance-filtered —
    if the destination is >500 km from the listener, skip it.
    Letters without coords (e.g. '任何地方') are always eligible.
    """
    pool = _load_letters()
    if not pool:
        return None
    # Distance filter
    if listener_lat != 0.0 or listener_lon != 0.0:
        near_pool = []
        for letter in pool:
            dlat = letter.get("dest_lat")
            dlon = letter.get("dest_lon")
            if dlat is None or dlon is None:
                near_pool.append(letter)  # no coords = always eligible
            elif _haversine_km((listener_lat, listener_lon), (dlat, dlon)) <= 500:
                near_pool.append(letter)
        if not near_pool:
            return None  # no local letters — quiet, don't force distant ones
        pool = near_pool
    return rng.choice(pool)


def take_letter(letter: dict, sim_time: datetime) -> dict:
    """Package a letter into an errand dict for state.errand."""
    return {
        "kind": "letter",
        "sender": letter["sender"],
        "recipient_desc": letter["recipient"],
        "hint": letter["hint"],
        "text": letter["text"],
        "taken_at": sim_time.isoformat(),
    }


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine distance in km."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    d = min(d, 1.0)
    return 2 * 6371.0 * math.asin(math.sqrt(d))


def check_delivery(pos: tuple[float, float], errand: dict,
                   place_coords: dict[str, tuple[float, float]],
                   radius_km: float = 5.0) -> str | None:
    """Check if current position is within radius_km of a place matching the
    errand's hint. Returns the matched place name or None.

    place_coords: {place_name: (lat, lon)} mapping from explorable_index etc.
    """
    if errand.get("kind") != "letter":
        return None
    hint = errand.get("hint", "")
    for name, coords in place_coords.items():
        if _haversine_km(pos, coords) <= radius_km:
            return name
    return None


def build_delivery_journal(errand: dict, place: str,
                           taken_at_iso: str,
                           delivered_at: datetime) -> str:
    """Build a one-line journal entry for a successful delivery."""
    try:
        taken = datetime.fromisoformat(taken_at_iso)
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=timezone.utc)
        delta_days = (delivered_at - taken).days
    except (ValueError, TypeError):
        delta_days = 0
    sender = errand.get("sender", "无名")
    if delta_days > 0:
        return f"{sender}的信送到了{place}。晚了{delta_days}天。"
    return f"{sender}的信送到了{place}。当天就到了。"


def create_chain(note: str, pos: tuple[float, float],
                 sim_time: datetime, rng: random.Random) -> dict:
    """Create a treasure chain errand from a bury note containing '下一个'."""
    chain_id = rng.randint(10000, 99999)
    return {
        "kind": "chain",
        "id": chain_id,
        "leg": 1,
        "note": note,
        "origin_pos": list(pos),
        "created_at": sim_time.isoformat(),
    }


def advance_chain(errand: dict) -> dict:
    """Advance a chain to the next leg. Returns updated errand."""
    new = dict(errand)
    new["leg"] = errand.get("leg", 1) + 1
    return new


def chain_is_terminal(errand: dict) -> bool:
    """Check if a chain has reached its maximum leg (3)."""
    return errand.get("leg", 0) >= 3


def chain_terminal_note() -> str:
    """The note left at the final station of a chain."""
    return "盒子空着,留给下一个写字的人。"


def create_festival_rumor(place: str, festival_name: str,
                          days_away: int) -> str:
    """Build a wind-mention text for a nearby festival."""
    if days_away <= 0:
        return f"电台里在说,{place}的{festival_name}今天开幕了。"
    return f"电台里在说,{days_away}天后{place}有{festival_name}。"


def letter_wait_text(rng: random.Random) -> str:
    """A subtle hint that you're carrying a letter, for wait scenes."""
    variants = [
        "包里的信纸摩擦出一点声音。",
        "你摸了一下包,信还在。",
        "风把信封的边角吹卷了。",
        "信纸贴着后背,有点潮。",
    ]
    return rng.choice(variants)


def errand_hint_line(errand: dict | None) -> str:
    """A one-line hint for where_am_i when carrying an errand."""
    if errand is None:
        return ""
    kind = errand.get("kind")
    if kind == "letter":
        hint = errand.get("hint", "")
        return f"你带着一封信,去{hint}的地方。"
    if kind == "chain":
        leg = errand.get("leg", 1)
        return f"你带着一个铁盒,第{leg}站。"
    return ""
