"""烘焙物产层——收割一次的真数据 × 手写模板。

数据: tools/harvest_local.py 收割,进 git,离线。
模板: 又又的手笔。数据是收的,文气是策划的。
"""

from __future__ import annotations

import json
import pathlib
import random

_DATA = pathlib.Path(__file__).resolve().parent / "data"

_food: dict | None = None
_flora: dict | None = None
_FOOD_SCENES: dict[str, str] | None = None  # name → description


def _load() -> None:
    global _food, _flora
    if _food is None:
        fp = _DATA / "food_by_country.json"
        _food = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    if _flora is None:
        fp = _DATA / "flora_by_place.json"
        _flora = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}


def _load_food_scenes() -> dict[str, str]:
    global _FOOD_SCENES
    if _FOOD_SCENES is None:
        _FOOD_SCENES = {}
        fp = _DATA / "food.txt"
        if fp.exists():
            for line in fp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "：" in line:
                    name, desc = line.split("：", 1)
                    _FOOD_SCENES[name.strip()] = desc.strip()
        else:
            # Fallback: try old split files
            for fname in ["food_east_asia.txt", "food_europe_middleeast.txt", "food_americas_africa_oceania.txt"]:
                fp2 = _DATA / fname
                if fp2.exists():
                    for line in fp2.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "：" in line:
                            name, desc = line.split("：", 1)
                            _FOOD_SCENES[name.strip()] = desc.strip()
    return _FOOD_SCENES


_FOOD_TEMPLATES: list[str] = [
    "点一份{name}。{desc}在这里,吃饭不是将就的事。",
    "{name}。{desc}本地的做法,和别处不一样。",
    "路边就有{name}。{desc}本地人当日常,旅人当特产。",
    "饿了的话,{name}是不会错的选择。{desc}",
    "{name}。{desc}名字记不住没关系,味道会替你记住。",
]

_FOOD_TEMPLATES_NODESC: list[str] = [
    "点一份{name}。在这里,吃饭不是将就的事。",
    "{name},本地的做法,和别处不一样。",
    "路边就有{name},本地人当日常,旅人当特产。",
    "饿了的话,{name}是不会错的选择。",
    "{name}。名字记不住没关系,味道会替你记住。",
]

_FLORA_TEMPLATES: list[str] = [
    "这一带多{name},是这里的原住民。",
    "路边最多的是{name},本地人看都不看一眼。",
    "{name},在这里长了几千年,比任何建筑都老。",
    "{name}站在路边,是给懂的人看的。",
]


# 常见美食字繁→简(zh-hans 标签缺的时候兜底)
_T2S = str.maketrans(
    "餅溫麵雞燒飯魚豬醬鹽蝦鴨鵝腸麥餃湯羅漢齋鬆蔥團鴛鴦捲餛飩鍋燉滷臘醃鮮餡鴿鰻鱈鮑魷龍鳳棗蓮絲頭條線餚類醬",
    "饼温面鸡烧饭鱼猪酱盐虾鸭鹅肠麦饺汤罗汉斋松葱团鸳鸯卷馄饨锅炖卤腊腌鲜馅鸽鳗鳕鲍鱿龙凤枣莲丝头条线肴类酱",
)


def _simp(s: str) -> str:
    return s.translate(_T2S)


def _has_cjk(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


def food_items(country_code: str | None, lat: float = 0, lon: float = 0) -> list[dict]:
    _load()
    if not country_code:
        return []
    items = _food.get(country_code, [])
    # 中国食物按地区过滤
    if country_code == "CN" and lat != 0:
        region = _get_cn_region(lat, lon)
        if region:
            filtered = [i for i in items if i.get("region") == region]
            if filtered:
                return filtered
    return items


def _get_cn_region(lat: float, lon: float) -> str:
    """根据经纬度判断中国地区"""
    regions = {
        "东北": (40, 55, 120, 135),
        "华北": (35, 45, 110, 120),
        "华东": (25, 35, 115, 125),
        "华南": (18, 25, 105, 120),
        "华中": (25, 35, 105, 115),
        "西北": (35, 50, 75, 110),
        "西南": (18, 35, 85, 110),
    }
    for name, (lat_min, lat_max, lon_min, lon_max) in regions.items():
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return ""


def flora_items(place_name: str | None) -> list[dict]:
    _load()
    if not place_name:
        return []
    return _flora.get(place_name, [])


def render_food(item: dict, rng: random.Random) -> str:
    name = _simp(item.get("zh") or item.get("en") or "")

    # 1. Try scene file first (most specific)
    scenes = _load_food_scenes()
    if name in scenes:
        return f"{name}。{scenes[name]}"

    # 2. Try partial match (e.g., "冬阴功汤" matches "冬阴功")
    for scene_name, scene_desc in scenes.items():
        if scene_name in name or name in scene_name:
            return f"{name}。{scene_desc}"

    # 3. Fall back to existing template logic
    desc = (item.get("desc") or "").strip()
    if not _has_cjk(desc):
        desc = ""  # 英文描述打断文气,宁缺
    if desc and not desc.endswith("。"):
        desc += "。"
    if desc:
        tmpl = rng.choice(_FOOD_TEMPLATES)
        return tmpl.format(name=name, desc=desc)
    tmpl = rng.choice(_FOOD_TEMPLATES_NODESC)
    return tmpl.format(name=name)


def render_flora(item: dict, rng: random.Random) -> str:
    name = item.get("zh") or item.get("la") or ""
    tmpl = rng.choice(_FLORA_TEMPLATES)
    return tmpl.format(name=name)
