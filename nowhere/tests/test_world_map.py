"""world110m.js 生成物结构体检(不联网)。"""

from __future__ import annotations

import json
import pathlib
import re


def test_world_map_artifact():
    p = pathlib.Path(__file__).resolve().parent.parent / "static" / "world110m.js"
    assert p.exists(), "先跑 python tools/build_world_map.py"
    text = p.read_text(encoding="utf-8")
    m = re.search(r"window\.WORLD_COUNTRIES = (.*);\n", text)
    assert m, "必须有 WORLD_COUNTRIES 行"
    countries = json.loads(m.group(1))
    assert len(countries) > 150, "国家数不对"
    for c in countries:
        assert c["n"] and c["z"], "国名中英都要"
        lon, lat = c["c"]
        assert -180 <= lon <= 180 and -90 <= lat <= 90
        assert len(c["r"]) >= 1
        for ring in c["r"]:
            assert len(ring) >= 4
    china = [c for c in countries if c["z"] == "中华人民共和国"]
    assert china, "中国在"


def test_world_map_roads_and_cities():
    p = pathlib.Path(__file__).resolve().parent.parent / "static" / "world110m.js"
    text = p.read_text(encoding="utf-8")
    roads = json.loads(re.search(r"window\.WORLD_ROADS = (.*);\n", text).group(1))
    cities = json.loads(re.search(r"window\.WORLD_CITIES = (.*);\n", text).group(1))
    assert len(roads) > 5000, "路网密度不对"
    for line in roads[:100]:
        assert len(line) >= 2
        for lon, lat in line:
            assert -180 <= lon <= 180 and -90 <= lat <= 90
    assert len(cities) > 50, "大城市太少"
    zh = {c["z"] for c in cities}
    for want in ("北京", "东京", "巴黎", "开罗", "纽约", "悉尼"):
        assert want in zh, f"{want} 该在"


def test_world_map_names_cn_stance():
    """国名按中国立场: 台湾/香港/澳门标中国,不许出现'中华民国'。"""
    p = pathlib.Path(__file__).resolve().parent.parent / "static" / "world110m.js"
    text = p.read_text(encoding="utf-8")
    assert "中华民国" not in text
    countries = json.loads(re.search(r"window\.WORLD_COUNTRIES = (.*);\n", text).group(1))
    by_en = {c["n"]: c["z"] for c in countries}
    assert by_en["Taiwan (China)"] == "中国台湾"
    assert by_en["Hong Kong (China)"] == "中国香港"
    assert by_en["Macau (China)"] == "中国澳门"
