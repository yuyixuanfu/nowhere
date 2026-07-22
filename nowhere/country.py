"""离线国家码查询——用买断的 GeoNames cities15000 就近推断。

数据在 nowhere/data/packs/cities15000.txt(gitignored,资源包)。
包不在就返回 None,调用方走自己的降级路径,不炸。
"""

from __future__ import annotations

import math
import pathlib

_PACK_PATH = pathlib.Path(__file__).resolve().parent / "data" / "packs" / "cities15000.txt"

_cities: list[tuple[float, float, str]] | None = None
_loaded = False


def _load() -> None:
    global _cities, _loaded
    if _loaded:
        return
    _loaded = True
    _cities = []
    if not _PACK_PATH.exists():
        return
    with open(_PACK_PATH, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue
            try:
                _cities.append((float(parts[4]), float(parts[5]), parts[8]))
            except ValueError:
                continue


def country_code_of(lat: float, lon: float) -> str | None:
    """返回最近城市的 ISO 国家码(如 "VN");数据包缺失返回 None。

    4 万城市线性扫,几毫秒,够用了。
    """
    _load()
    if not _cities:
        return None
    best_cc: str | None = None
    best_d = math.inf
    for clat, clon, cc in _cities:
        d = (clat - lat) ** 2 + ((clon - lon) * math.cos(math.radians(lat))) ** 2
        if d < best_d:
            best_d = d
            best_cc = cc
    return best_cc
