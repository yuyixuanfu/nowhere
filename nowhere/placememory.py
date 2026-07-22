"""地方记忆——地方记得你来过,也记得你见过什么。

存在 NOWHERE_HOME 下:
- seen_cards.json: {地名: [已见方志卡 key]}
- seen_humanities.json: [已见人文卡 key] (全局,不按地名)
- visits.json: {地名: 次数}
- landings.json: 落点编录
- sightings.json: 动物目击编录
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _path(name: str) -> Path:
    base = os.environ.get("NOWHERE_HOME") or str(Path.home() / ".nowhere")
    return Path(base) / name


def _load(name: str) -> dict:
    p = _path(name)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _dump(name: str, data: dict) -> None:
    p = _path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def seen_cards(place: str) -> set[str]:
    return set(_load("seen_cards.json").get(place, []))


def save_seen_cards(place: str, cards: set[str]) -> None:
    data = _load("seen_cards.json")
    data[place] = sorted(cards)
    _dump("seen_cards.json", data)


def record_visit(place: str) -> int:
    """记一次到访,返回这是第几次。"""
    data = _load("visits.json")
    data[place] = data.get(place, 0) + 1
    _dump("visits.json", data)
    return data[place]


def record_landing(
    place: str,
    lat: float,
    lon: float,
    elevation: float | None = None,
    surface: str | None = None,
) -> int:
    """落点编录: 地名+坐标+次数+最近一次+地貌(地图画地形符号用)。返回第几次来。"""
    from datetime import datetime, timezone

    data = _load("landings.json")
    entry = data.get(place, {"lat": round(lat, 4), "lon": round(lon, 4), "count": 0})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["lat"] = round(lat, 4)
    entry["lon"] = round(lon, 4)
    if elevation is not None:
        entry["elevation"] = round(elevation)
    if surface:
        entry["surface"] = surface
    entry["last"] = datetime.now(timezone.utc).isoformat()
    data[place] = entry
    _dump("landings.json", data)
    return entry["count"]


def landings() -> list[dict]:
    """全部落点,新的在前。"""
    data = _load("landings.json")
    items = [{"place": k, **v} for k, v in data.items()]
    items.sort(key=lambda x: x.get("last", ""), reverse=True)
    return items


def record_sighting(
    name: str,
    common_name: str,
    lat: float,
    lon: float,
    distance_m: int | None,
    seen_at: str,
    source: str,
) -> None:
    """动物目击编录: 谁/在哪/多远/哪天/来源。上限 200 条。"""
    from datetime import datetime, timezone

    data = _load("sightings.json")
    items = data.get("items", [])
    items.append({
        "name": name,
        "common_name": common_name,
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "distance_m": distance_m,
        "seen_at": seen_at,
        "source": source,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    data["items"] = items[-200:]
    _dump("sightings.json", data)


def sightings() -> list[dict]:
    """全部目击,新的在前。"""
    return list(reversed(_load("sightings.json").get("items", [])))


def seen_humanities() -> set[str]:
    """Load the global set of seen humanities card keys."""
    data = _load("seen_humanities.json")
    return set(data.get("keys", []))


def save_seen_humanities(keys: set[str]) -> None:
    """Persist the global set of seen humanities card keys."""
    _dump("seen_humanities.json", {"keys": sorted(keys)})


# ── 明信片落盘: 文件是真相,谁寄的网页都看得见 ─────────────────────

_POSTCARDS_CAP = 100


def save_postcard(card: dict) -> None:
    """寄出即落盘。跨进程跨会话,墙不空。"""
    data = _load("postcards.json")
    items = data.get("items", [])
    items.append(card)
    data["items"] = items[-_POSTCARDS_CAP:]
    _dump("postcards.json", data)


def update_postcard(card: dict) -> None:
    """卡内容变了(正面图生成好了)就回写。"""
    data = _load("postcards.json")
    items = data.get("items", [])
    for i, c in enumerate(items):
        if c.get("id") == card.get("id"):
            items[i] = card
            break
    data["items"] = items
    _dump("postcards.json", data)


def add_postcard_reply(card_id: int, content: str) -> bool:
    """人回一句,落盘。卡在不在文件里,不在就 False。"""
    data = _load("postcards.json")
    items = data.get("items", [])
    for c in items:
        if c.get("id") == card_id:
            c.setdefault("replies", []).append(content)
            data["items"] = items
            _dump("postcards.json", data)
            return True
    return False


def postcards() -> list[dict]:
    """全部明信片,新的在前。文件空时试着从 state.json 搬一次家。"""
    items = _load("postcards.json").get("items", [])
    if not items:
        state_file = _path("state.json")
        if state_file.exists():
            try:
                import json as _json

                old = _json.loads(state_file.read_text(encoding="utf-8"))
                items = old.get("postcards", [])
                if items:
                    _dump("postcards.json", {"items": items[-_POSTCARDS_CAP:]})
            except (OSError, _json.JSONDecodeError):
                pass
    return list(reversed(items))


def delete_postcard(card_id: int) -> bool:
    """撕掉一张。测试卡、废卡,别留在墙上。"""
    data = _load("postcards.json")
    items = data.get("items", [])
    keep = [c for c in items if c.get("id") != card_id]
    if len(keep) == len(items):
        return False
    data["items"] = keep
    _dump("postcards.json", data)
    return True
