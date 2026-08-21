"""离线国家码查询——用买断的 GeoNames cities15000 就近推断。

数据在 nowhere/data/packs/cities15000.txt(gitignored,资源包)。
包不在就返回 None,调用方走自己的降级路径,不炸。
"""

from __future__ import annotations

from nowhere import city_index


def country_code_of(lat: float, lon: float) -> str | None:
    """返回最近城市的 ISO 国家码(如 "VN");数据包缺失返回 None。"""
    return city_index.country_of(lat, lon)
