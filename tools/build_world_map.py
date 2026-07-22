"""一次性构建: Natural Earth → world110m.js(世界地图全部数据)。

只在换数据源时手动跑: python tools/build_world_map.py
生成物提交进仓库,运行时零网络。

输出一个文件,三层数据:
- window.WORLD_COUNTRIES: 国家 {n,z,c,r}(50m 边界,中文名,标注点)
- window.WORLD_ROADS:    主干路网 [[[lon,lat],...], ...](10m,scalerank<=4)
- window.WORLD_CITIES:   大城市 {n,z,c}(手工中文名,全球铺开)

国名补丁按中国立场(台湾/香港/澳门标中国)。南极洲不上墙。
"""

from __future__ import annotations

import json
import pathlib
import urllib.request

_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
OUT = pathlib.Path(__file__).resolve().parent.parent / "nowhere" / "static" / "world110m.js"

# 国名补丁: 数据源是西方视角,这里按中国立场改
_NAME_PATCH = {
    "Taiwan": ("Taiwan (China)", "中国台湾"),
    "Hong Kong": ("Hong Kong (China)", "中国香港"),
    "Macau": ("Macau (China)", "中国澳门"),
    "Kosovo": ("Kosovo (Serbia)", "科索沃(塞尔维亚)"),
}

# 大城市中文名(英文 NAME → 中文)。没进表的不上图。
_CITY_LABELS = {
    "Beijing": "北京", "Shanghai": "上海", "Guangzhou": "广州", "Shenzhen": "深圳",
    "Chongqing": "重庆", "Chengdu": "成都", "Hong Kong": "香港", "Taipei": "台北",
    "Tokyo": "东京", "Osaka": "大阪", "Kyoto": "京都", "Seoul": "首尔",
    "Singapore": "新加坡", "Bangkok": "曼谷", "Jakarta": "雅加达",
    "Mumbai": "孟买", "Delhi": "德里", "Kolkata": "加尔各答", "Kabul": "喀布尔",
    "Tehran": "德黑兰", "Baghdad": "巴格达", "Istanbul": "伊斯坦布尔",
    "Moscow": "莫斯科", "Saint Petersburg": "圣彼得堡",
    "Berlin": "柏林", "Paris": "巴黎", "London": "伦敦", "Madrid": "马德里",
    "Rome": "罗马", "Athens": "雅典", "Vienna": "维也纳", "Prague": "布拉格",
    "Warsaw": "华沙", "Stockholm": "斯德哥尔摩", "Oslo": "奥斯陆",
    "Copenhagen": "哥本哈根", "Amsterdam": "阿姆斯特丹", "Zurich": "苏黎世",
    "Lisbon": "里斯本", "Dublin": "都柏林", "Reykjavik": "雷克雅未克",
    "Cairo": "开罗", "Lagos": "拉各斯", "Nairobi": "内罗毕",
    "Johannesburg": "约翰内斯堡", "Cape Town": "开普敦", "Casablanca": "卡萨布兰卡",
    "New York": "纽约", "Los Angeles": "洛杉矶", "San Francisco": "旧金山",
    "Chicago": "芝加哥", "Toronto": "多伦多", "Vancouver": "温哥华",
    "Mexico City": "墨西哥城", "Havana": "哈瓦那",
    "Sao Paulo": "圣保罗", "Rio de Janeiro": "里约热内卢",
    "Buenos Aires": "布宜诺斯艾利斯", "Santiago": "圣地亚哥", "Lima": "利马",
    "Bogota": "波哥大", "Sydney": "悉尼", "Melbourne": "墨尔本", "Auckland": "奥克兰",
}


def _fetch(name: str) -> dict:
    cache_dir = pathlib.Path(__file__).resolve().parent / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{name}.geojson"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    raw = urllib.request.urlopen(f"{_BASE}/{name}.geojson", timeout=180).read()
    cache.write_bytes(raw)
    return json.loads(raw)


def _round_ring(ring: list, nd: int = 2) -> list:
    out = []
    for lon, lat, *_ in ring:
        pt = [round(lon, nd), round(lat, nd)]
        if not out or out[-1] != pt:
            out.append(pt)
    return out


def _centroid(ring: list) -> list:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return [round(sum(xs) / len(xs), 2), round(sum(ys) / len(ys), 2)]


def _countries() -> list[dict]:
    gj = _fetch("ne_50m_admin_0_countries")
    countries: list[dict] = []
    for feat in gj["features"]:
        props = feat["properties"]
        geom = feat["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        rings = []
        for poly in polys:
            ring = _round_ring(poly[0])
            if len(ring) >= 4:
                rings.append(ring)
        if not rings:
            continue
        if max(p[1] for r in rings for p in r) < -60:
            continue  # 南极洲不上墙
        lx, ly = props.get("LABEL_X"), props.get("LABEL_Y")
        if lx is None or ly is None:
            biggest = max(rings, key=len)
            lx, ly = _centroid(biggest)
        else:
            lx, ly = round(float(lx), 2), round(float(ly), 2)
        name_en = props.get("NAME_EN") or props.get("NAME") or ""
        name_zh = props.get("NAME_ZH") or props.get("NAME") or ""
        if name_en in _NAME_PATCH:
            name_en, name_zh = _NAME_PATCH[name_en]
        countries.append({"n": name_en, "z": name_zh, "c": [lx, ly], "r": rings})
    return countries


def _roads() -> list[list[list[float]]]:
    gj = _fetch("ne_10m_roads")
    lines: list[list[list[float]]] = []
    for feat in gj["features"]:
        props = feat["properties"]
        if int(props.get("scalerank") or 99) > 3:
            continue  # 只要顶级走廊,其余是噪点
        geom = feat["geometry"]
        parts = geom["coordinates"] if geom["type"] == "MultiLineString" else [geom["coordinates"]]
        for part in parts:
            line = _round_ring(part, nd=2)
            if len(line) >= 2:
                lines.append(line)
    return lines


def _cities() -> list[dict]:
    gj = _fetch("ne_10m_populated_places")
    best: dict[str, dict] = {}
    for feat in gj["features"]:
        props = feat["properties"]
        name = props.get("NAME") or ""
        if name not in _CITY_LABELS:
            continue
        # 重名消歧: London 有英国的和加拿大的——按 首都>世界城市>人口 挑正主
        score = (
            int(props.get("ADM0CAP") or 0) * 10**12
            + int(props.get("WORLDCITY") or 0) * 10**11
            + int(props.get("MEGACITY") or 0) * 10**10
            + int(props.get("POP_MAX") or 0)
        )
        if name in best and best[name]["_score"] >= score:
            continue
        lon, lat = feat["geometry"]["coordinates"][:2]
        best[name] = {
            "n": name,
            "z": _CITY_LABELS[name],
            "c": [round(lon, 2), round(lat, 2)],
            "big": 1 if (props.get("MEGACITY") or props.get("ADM0CAP") or props.get("WORLDCITY")) else 0,
            "_score": score,
        }
    for c in best.values():
        del c["_score"]
    return list(best.values())


def _rivers() -> list[list[list[float]]]:
    gj = _fetch("ne_10m_rivers_lake_centerlines")
    lines: list[list[list[float]]] = []
    for feat in gj["features"]:
        props = feat["properties"]
        if int(props.get("scalerank") or 99) > 4:
            continue
        geom = feat["geometry"]
        parts = geom["coordinates"] if geom["type"] == "MultiLineString" else [geom["coordinates"]]
        for part in parts:
            line = _round_ring(part, nd=2)
            if len(line) >= 2:
                lines.append(line)
    return lines


def _lakes() -> list[list[list[float]]]:
    gj = _fetch("ne_50m_lakes")
    rings: list[list[list[float]]] = []
    for feat in gj["features"]:
        geom = feat["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            ring = _round_ring(poly[0])
            if len(ring) >= 4:
                rings.append(ring)
    return rings


def _terrain_symbols() -> list[list]:
    """从 grid_tiny.npz 撒手绘地貌符号——山/雪/树/沙,全世界都长出来。

    1° 网格抽样,抖动打散;上限控制文件体积。
    输出 [[lon, lat, kind], ...],kind: m=山 s=雪 t=树 d=沙
    """
    import numpy as np

    grid = pathlib.Path(__file__).resolve().parent.parent / "nowhere" / "data" / "grid_tiny.npz"
    d = np.load(grid)
    elev, cover = d["elev"], d["cover"]

    picks: dict[str, list[list[float]]] = {"m": [], "s": [], "t": [], "d": []}
    rows, cols = elev.shape
    for r in range(rows):
        lat = 90.0 - r
        if lat < -60:
            continue  # 南极不上墙
        for c in range(cols):
            lon = c - 180.0
            cv = int(cover[r, c])
            e = float(elev[r, c])
            if cv in (4, 5):
                picks["s"].append([lon, lat])
            elif e >= 2600 or cv == 2:
                picks["m"].append([lon, lat])
            elif cv == 6:
                picks["t"].append([lon, lat])
            elif cv == 3:
                picks["d"].append([lon, lat])

    # 聚类过滤: 2.5° 格内同类符号数不够就丢——树要成林,沙要成海
    import math

    min_cluster = {"m": 2, "s": 2, "t": 3, "d": 3}
    for kind, cells in picks.items():
        cells.sort()
        grid_count: dict[tuple[int, int], int] = {}
        for lon, lat in cells:
            key = (math.floor(lon / 2.5), math.floor(lat / 2.5))
            grid_count[key] = grid_count.get(key, 0) + 1
        picks[kind] = [
            [lon, lat] for lon, lat in cells
            if grid_count[(math.floor(lon / 2.5), math.floor(lat / 2.5))] >= min_cluster[kind]
        ]

    caps = {"m": 260, "s": 130, "t": 260, "d": 150}
    out: list[list] = []
    for kind, cells in picks.items():
        cap = caps[kind]
        stride = max(1, len(cells) // cap + (1 if len(cells) % cap else 0))
        for i, (lon, lat) in enumerate(cells):
            if i % stride:
                continue
            # 抖动打散格子感 + 大小起伏,手绘不是阅兵
            jx = (((i * 73 + 11) % 100) / 100 - 0.5) * 1.8
            jy = (((i * 37 + 29) % 100) / 100 - 0.5) * 1.6
            sc = 0.65 + ((i * 53) % 60) / 100  # 0.65-1.25
            out.append([round(lon + jx, 2), round(lat + jy, 2), kind, round(sc, 2)])
    return out


def main() -> None:
    countries = _countries()
    roads = _roads()
    cities = _cities()
    rivers = _rivers()
    lakes = _lakes()
    terrain = _terrain_symbols()
    payload = (
        "window.WORLD_COUNTRIES = " + json.dumps(countries, ensure_ascii=False, separators=(",", ":")) + ";\n"
        + "window.WORLD_ROADS = " + json.dumps(roads, separators=(",", ":")) + ";\n"
        + "window.WORLD_RIVERS = " + json.dumps(rivers, separators=(",", ":")) + ";\n"
        + "window.WORLD_LAKES = " + json.dumps(lakes, separators=(",", ":")) + ";\n"
        + "window.WORLD_TERRAIN = " + json.dumps(terrain, separators=(",", ":")) + ";\n"
        + "window.WORLD_CITIES = " + json.dumps(cities, ensure_ascii=False, separators=(",", ":")) + ";\n"
    )
    OUT.write_text(payload, encoding="utf-8")
    print(f"{len(countries)} countries, {len(roads)} roads, {len(rivers)} rivers, {len(lakes)} lakes, {len(terrain)} terrain syms, {len(cities)} cities -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
