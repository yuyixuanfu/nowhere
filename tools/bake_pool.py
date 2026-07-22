"""把落点池的真实海拔+地表烘进 pool.json(手核的真实数据)。

tiny 网格(1°)在落点上误差太大——喀什说成 300m 林地(实际 1290m 绿洲)。
烘完后 terrain 在落点附近优先用池里的真实值。
跑一次即可: python tools/bake_pool.py
"""

import json
import pathlib

POOL = pathlib.Path(__file__).resolve().parent.parent / "portal" / "data" / "pool.json"

# name_hint -> (elev_m, surface)
REAL: dict[str, tuple[float, str]] = {
    # volcano
    "富士山": (3776, "rock"), "长白山": (2600, "rock"), "维苏威火山": (1281, "rock"),
    "乞力马扎罗": (5895, "ice"), "埃特纳火山": (3329, "rock"), "莫纳罗亚": (4169, "rock"),
    "皮纳图博": (1486, "rock"), "艾雅法拉": (1666, "ice"),
    # coast
    "大堡礁": (0, "water_ocean"), "好望角": (50, "rock"), "鳕鱼角": (10, "sand"),
    "诺曼底": (30, "sand"), "阿马尔菲": (80, "rock"), "大苏尔": (200, "rock"),
    "下龙湾": (20, "rock"), "里约热内卢": (5, "sand"),
    # rainforest
    "亚马逊雨林": (60, "forest"), "刚果雨林": (400, "forest"), "婆罗洲": (100, "forest"),
    "巴布亚": (500, "forest"), "丹翠雨林": (200, "forest"), "亚苏尼": (250, "forest"),
    "蒙特维多": (1400, "forest"), "辛哈拉加": (500, "forest"),
    # desert
    "撒哈拉沙漠": (400, "sand"), "戈壁滩": (1100, "bare"), "阿塔卡马": (2000, "bare"),
    "纳米布沙漠": (600, "sand"), "莫哈韦沙漠": (600, "sand"), "阿拉伯沙漠": (300, "sand"),
    "塔尔沙漠": (150, "sand"), "辛普森沙漠": (100, "sand"),
    # tundra
    "西伯利亚冻原": (100, "grass"), "格陵兰": (2000, "ice"), "斯瓦尔巴群岛": (400, "snow"),
    "阿拉斯加北坡": (50, "grass"), "拉普兰": (300, "grass"), "加拿大北极": (100, "grass"),
    "堪察加冻原": (300, "grass"), "巴塔哥尼亚草原": (200, "grass"),
    # city
    "东京": (20, "urban"), "喀什": (1290, "urban"), "内罗毕": (1660, "urban"),
    "瓦尔帕莱索": (40, "urban"), "伊斯坦布尔": (40, "urban"), "雷克雅未克": (15, "urban"),
    "马拉喀什": (470, "urban"), "乌兰巴托": (1300, "urban"),
    # island
    "冰岛": (300, "rock"), "法罗群岛": (300, "grass"), "复活节岛": (100, "grass"),
    "马达加斯加": (800, "grass"), "斐济": (100, "forest"), "加拉帕戈斯": (200, "rock"),
    "亚速尔群岛": (300, "grass"), "设得兰群岛": (100, "grass"),
    # mountain
    "珠峰大本营": (5364, "rock"), "阿尔卑斯霞慕尼": (1035, "grass"),
    "K2大本营": (4900, "rock"), "德纳利": (6190, "snow"), "阿空加瓜": (6961, "rock"),
    "勃朗峰": (4808, "ice"), "乞力马扎罗山顶": (5895, "ice"), "安纳普尔纳": (8091, "snow"),
}


def main() -> None:
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    missing = []
    for entry in pool:
        real = REAL.get(entry["name_hint"])
        if real is None:
            missing.append(entry["name_hint"])
            continue
        entry["elev_m"], entry["surface"] = real
    if missing:
        raise SystemExit(f"缺映射: {missing}")
    POOL.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"baked {len(pool)} entries")


if __name__ == "__main__":
    main()
