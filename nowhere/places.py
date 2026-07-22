"""命名地点——places.db(SQLite)+ 俗名补丁。

数据: tools/import_geonames.py 从买断的 GeoNames 全量导入。
库不在就全部返回空/None,降级不炸。
"""

from __future__ import annotations

import json
import math
import pathlib
import sqlite3

_DATA = pathlib.Path(__file__).resolve().parent / "data"


def _resolve_db() -> pathlib.Path:
    """places.db 可能在 D 盘(C 盘瘦身搬家)——本地优先,其次 D,最后环境变量。"""
    local = _DATA / "places.db"
    if local.exists():
        return local
    import os

    env = os.environ.get("NOWHERE_DATA")
    if env and (pathlib.Path(env) / "places.db").exists():
        return pathlib.Path(env) / "places.db"
    d_drive = pathlib.Path(r"D:\Users\chat\nowhere_data\places.db")
    return d_drive  # 不存在也返回它,报错信息里能看出在找哪


_DB = _resolve_db()
_PATCH = _DATA / "places_patch.json"

_TYPE_ZH: dict[str, str] = {
    "MT": "山", "MTS": "山脉", "PK": "峰", "HLL": "丘", "VAL": "谷",
    "STM": "河", "LK": "湖", "OAS": "绿洲", "FLLS": "瀑布",
    "PRK": "公园", "RES": "保护区", "FOREST": "林",
    "MNMT": "纪念碑", "RUIN": "遗址", "CSTL": "城堡", "MUS": "博物馆",
    "PAL": "宫殿", "CH": "教堂", "TMPL": "寺", "MSQE": "清真寺",
    "SQR": "广场", "BDG": "桥", "TOWR": "塔",
    "PPL": "城镇", "PPLA": "城镇", "PPLA2": "城镇", "PPLA3": "城镇",
    "PPLC": "城市", "PPLL": "村子", "PPLX": "街区",
}

_compass = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]


def _bearing_word(deg: float) -> str:
    return _compass[round(deg / 45) % 8]


def _haversine_km(a_lat, a_lon, b_lat, b_lon) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a_lat, a_lon, b_lat, b_lon))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _bearing_deg(a_lat, a_lon, b_lat, b_lon) -> float:
    lat1, lat2 = math.radians(a_lat), math.radians(b_lat)
    dlon = math.radians(b_lon - a_lon)
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _conn() -> sqlite3.Connection | None:
    if not _DB.exists():
        return None
    return sqlite3.connect(_DB)


def _patch() -> dict:
    if _PATCH.exists():
        return json.loads(_PATCH.read_text(encoding="utf-8"))
    return {}


def _row_to_dict(row, from_lat, from_lon) -> dict:
    pid, name, lat, lon, fclass, fcode = row
    dist = _haversine_km(from_lat, from_lon, lat, lon)
    bearing = _bearing_deg(from_lat, from_lon, lat, lon)
    return {
        "id": pid, "name": name, "lat": lat, "lon": lon,
        "type": _TYPE_ZH.get(fcode, fcode),
        "distance_km": round(dist, 1),
        "bearing_deg": round(bearing),
        "bearing": _bearing_word(bearing),
    }


# nearby 只留"有意义的地方": 自然(T/H/V)+地标类S+聚落P。
# 酒店餐厅商店是噪声,不要。
_LANDMARK_S = {
    "MNMT", "RUIN", "CSTL", "MUS", "PAL", "CH", "TMPL", "MSQE",
    "SQR", "TOWR", "BDG", "CMP", "OBS", "LGHT", "FRK", "PIER",
}


def nearby(lat: float, lon: float, radius_km: float = 20.0, limit: int = 10) -> list[dict]:
    """附近的命名地点,按距离排(滤掉商业 POI 噪声)。"""
    conn = _conn()
    if conn is None:
        return []
    try:
        deg = radius_km / 111.0
        rows = conn.execute(
            """SELECT id, name, lat, lon, fclass, fcode FROM places
               WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
               AND fclass IN ('S','T','H','V','P') LIMIT 3000""",
            (lat - deg, lat + deg, lon - deg, lon + deg),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        if row[4] == "S" and row[5] not in _LANDMARK_S:
            continue
        d = _row_to_dict(row, lat, lon)
        if d["distance_km"] <= radius_km and d["distance_km"] > 0.3:
            out.append(d)
    out.sort(key=lambda x: x["distance_km"])
    return out[:limit]


_STRIP_SUFFIXES = ("山顶", "山", "峰", "岭", "顶", "江", "河", "湖", "海", "城", "镇", "寺", "岛", "村", "县", "区")


def find(name: str, near: tuple[float, float] | None = None) -> dict | None:
    """按名字找地点: 补丁(含削词尾) → FTS → 削词尾 FTS。

    "富士山顶"先找补丁的"富士山"——策划过的名字永远赢 FTS 的长尾。
    """
    candidates = [name]
    for suffix in _STRIP_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            candidates.append(name[: -len(suffix)])

    # 第一遍: 补丁(精确 + 削词尾),策展永远赢
    for cand in candidates:
        hit = _patch_lookup(cand, near)
        if hit is not None:
            return hit

    # 第二遍: 上下文感知——短名字 + 当前位置 → 选最近的知名地标
    if near and len(name) <= 4:
        ctx = _contextual_match(name, near)
        if ctx:
            return ctx

    # 第三遍: FTS(精确 + 削词尾)
    for cand in candidates:
        hit = _fts_lookup(cand, near)
        if hit is not None:
            return hit

    return None


def _contextual_match(name: str, near: tuple[float, float]) -> dict | None:
    """上下文感知: 短名字 + 当前位置 → 匹配最近的知名地标。

    例: "天池" + 用户在长白山附近 → 长白山天池
    "湖" + 用户在日内瓦 → 日内瓦湖
    """
    _CONTEXTUAL: list[tuple[str, str, float, float]] = [
        # (搜索词, 匹配名, 匹配lat, 匹配lon)
        ("天池", "长白山天池", 42.0, 128.05),
        ("天池", "天山天池", 43.87, 88.12),
        ("湖", "日内瓦湖", 46.45, 6.5),
        ("湖", "贝加尔湖", 53.5, 108.0),
        ("湖", "维多利亚湖", -1.0, 33.0),
        ("湖", "的的喀喀湖", -15.8, -69.3),
        ("山", "富士山", 35.36, 138.73),
        ("山", "马特洪峰", 45.98, 7.66),
        ("山", "乞力马扎罗", -3.07, 37.35),
        ("河", "多瑙河", 48.2, 16.4),
        ("河", "长江", 30.0, 117.0),
        ("河", "尼罗河", 30.0, 31.2),
    ]
    lat, lon = near
    # Find all candidates matching the keyword
    candidates = []
    for keyword, match_name, mlat, mlon in _CONTEXTUAL:
        if name == keyword:
            dist = _haversine_km(lat, lon, mlat, mlon)
            candidates.append((dist, match_name))
    if candidates:
        candidates.sort()
        best_name = candidates[0][1]
        hit = _patch_lookup(best_name, near)
        if hit:
            return hit
    return None


def _patch_lookup(name: str, near: tuple[float, float] | None) -> dict | None:
    patch = _patch()
    patch_lower = {k.lower(): v for k, v in patch.items()}
    hit = patch.get(name) or patch_lower.get(name.lower())
    if not hit:
        return None
    result = {"name": name, "lat": hit["lat"], "lon": hit["lon"], "type": hit.get("type", "地标")}
    if near:
        result["distance_km"] = round(_haversine_km(near[0], near[1], hit["lat"], hit["lon"]), 1)
        result["bearing"] = _bearing_word(_bearing_deg(near[0], near[1], hit["lat"], hit["lon"]))
    return result


def _find_once(name: str, near: tuple[float, float] | None) -> dict | None:
    """单次查找(保留兼容): 补丁 → FTS。"""
    return _patch_lookup(name, near) or _fts_lookup(name, near)


def _fts_lookup(name: str, near: tuple[float, float] | None) -> dict | None:

    conn = _conn()
    if conn is None:
        return None
    try:
        rows = conn.execute(
            """SELECT p.id, p.name, p.ascii, p.lat, p.lon, p.fclass, p.fcode, p.pop
               FROM places_fts f JOIN places p ON p.id = f.rowid
               WHERE places_fts MATCH ? LIMIT 20""",
            (f'"{name}"',),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []  # 库还在建(FTS 未就绪),降级不炸
    finally:
        conn.close()
    if not rows:
        return None

    def _class_rank(fclass: str, fcode: str) -> int:
        if fcode in ("PPLC", "PPLA"):
            return 0  # 首府/省会最优先(Reykjavik 是雷克雅未克,不是加拿大哪个湖)
        if fclass in ("T", "H", "V"):
            return 1  # 自然地理(海峡是海峡,不是酒店)
        if fclass == "S" and fcode in _LANDMARK_S:
            return 2
        if fclass == "P":
            return 3
        return 4  # 商业 POI 最后

    q = name.lower()

    def _exact_rank(r) -> int:
        if r[1].lower() == q or (r[2] or "").lower() == q:
            return 0  # 名字/ascii 完全相等
        return 1

    if near:
        rows.sort(key=lambda r: (_exact_rank(r), _class_rank(r[5], r[6]),
                                 _haversine_km(near[0], near[1], r[3], r[4])))
    else:
        rows.sort(key=lambda r: (_exact_rank(r), _class_rank(r[5], r[6]), -(r[7] or 0)))
    r = rows[0]
    result = {
        "name": r[1], "lat": r[3], "lon": r[4],
        "type": _TYPE_ZH.get(r[6], r[6]),
    }
    if near:
        result["distance_km"] = round(_haversine_km(near[0], near[1], r[3], r[4]), 1)
        result["bearing"] = _bearing_word(_bearing_deg(near[0], near[1], r[3], r[4]))
    return result
