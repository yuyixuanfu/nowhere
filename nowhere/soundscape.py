"""声景——环境的声音,全部由真实数据推出,永远离线。

规则: 每个声音必须指得回数据字段。风=wind_ms,雨=precip,叶响=forest,
浪=水面/离岸,底噪=urban,虫鸣=夜晚+温暖+植被。编的一律不写。
"""

from __future__ import annotations

import random

_SURFACE_ZH: dict[str, str] = {
    "forest": "林地", "grass": "草地", "rock": "岩地", "sand": "沙地",
    "snow": "雪地", "ice": "冰面", "water_ocean": "海面", "water_fresh": "水边",
    "urban": "城市", "bare": "裸地", "wetland": "湿地",
}

_SURFACE_SOUND: dict[str, str] = {
    "forest": "树叶子哗哗地响",
    "grass": "草叶一阵一阵地伏下去",
    "rock": "石缝呜呜地叫",
    "bare": "碎石缝呜呜地叫",
    "snow": "雪面上什么声音都站不住",
    "ice": "冰面偶尔咔的一声,远远的",
    "sand": "沙被吹得贴着地皮走",
    "urban": "车声远远地滚,人声的底噪",
    "wetland": "水草叶子互相擦",
}


def describe_sound(env: dict, rng: random.Random) -> str:
    """输入环境快照 {"weather","surface","sky","mode"},输出一段声景散文。

    全安静也输出——静也是一种声景(辽阔和孤独也是信息)。
    """
    weather = env.get("weather") or {}
    sky = env.get("sky") or {}
    surface = env.get("surface", "")
    mode = env.get("mode", "land")

    wind = weather.get("wind_ms", 0)
    precip = weather.get("precip", "none")
    temp = weather.get("temp_c", 15)
    night = sky.get("phase") in ("night", "nautical")

    sounds: list[str] = []

    # 降水压过一切
    if precip == "rain":
        target = _SURFACE_SOUND.get(surface, "地")
        sounds.append(rng.choice([
            "雨声。雨点砸下来,把别的声音都盖住了。",
            f"雨一阵密一阵疏,落在{_SURFACE_ZH.get(surface, '地面')}上。",
            "雨。世界只剩这一种声音。",
        ]))
        return "".join(sounds)
    if precip == "snow":
        return rng.choice([
            "雪把声音都吃掉了。静。",
            "落雪无声。连自己呼吸都听得见。",
        ])

    # 风
    if wind >= 12:
        sounds.append(rng.choice([
            "风在吼,一阵紧过一阵。",
            "风声大,说话得贴着耳朵喊。",
        ]))
    elif wind >= 6:
        detail = _SURFACE_SOUND.get(surface)
        if detail:
            sounds.append(f"风起来了,{detail}。")
        else:
            sounds.append("风一阵一阵。")
    elif wind >= 2:
        sounds.append("风小,一阵一阵。")

    # 水
    if mode == "water" or surface in ("water_ocean",):
        sounds.append(rng.choice([
            "浪一下一下,把人托起来又放下去。",
            "水声就在耳边,一下一下。",
        ]))
    elif surface == "water_fresh":
        sounds.append("水声细,一下一下拍着岸。")

    # 城市底噪
    if surface == "urban" and wind < 6:
        sounds.append(_SURFACE_SOUND["urban"] + "。")

    # 夜+暖+植被 → 虫
    if night and temp > 15 and surface in ("forest", "grass", "wetland"):
        sounds.append("虫声一层一层的,不知疲倦。")

    if not sounds:
        return rng.choice([
            "四下无人。风是这里唯一的声音。",
            "静。静得能听见自己的心跳。",
            "什么声音也没有。世界好像只剩你一个。",
        ])

    return "".join(sounds)
