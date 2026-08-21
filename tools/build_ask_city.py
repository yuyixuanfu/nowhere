#!/usr/bin/env python3
"""Aggregate city-related data into a unified ask_city.json for the ask feature.

Reads from:
  - nowhere/data/localcolor.json   -> 美食
  - nowhere/data/humanities.json   -> 历史
  - nowhere/data/festivals.json    -> 节日
  - nowhere/data/seasonal.txt      -> 季节
  - nowhere/data/encounters.txt    -> 见闻
  - nowhere/data/ask_kb.json       -> 地标 (via keyword matching)

Outputs: nowhere/data/ask_city.json
"""

import json
import re
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent / "nowhere" / "data"
OUT_PATH = DATA_DIR / "ask_city.json"

# --- slug -> Chinese city name mapping for encounters.txt ---
SLUG_TO_CN = {
    "addis_ababa": "亚的斯亚贝巴",
    "bogota": "波哥大",
    "buenos-aires": "布宜诺斯艾利斯",
    "cairo": "开罗",
    "cape_town": "开普敦",
    "havana": "哈瓦那",
    "lagos": "拉各斯",
    "lima": "利马",
    "los-angeles": "洛杉矶",
    "marrakesh": "马拉喀什",
    "mexico-city": "墨西哥城",
    "nairobi": "内罗毕",
    "new-orleans": "新奥尔良",
    "new-york": "纽约",
    "rio": "里约",
    "san-francisco": "旧金山",
    "tunis": "突尼斯",
    "yemen": "也门",
    "zanzibar": "桑给巴尔",
}


def load_json(name: str):
    path = DATA_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_lines(name: str):
    path = DATA_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


# ---- 1. 美食: localcolor.json -> 美食 category ----

def collect_food() -> dict[str, list[str]]:
    """Collect food entries from localcolor.json."""
    lc = load_json("localcolor.json")
    result = {}
    for city, cats in lc.items():
        items = cats.get("美食", [])
        if items:
            result[city] = items
    return result


# ---- 2. 历史: humanities.json -> 事件 + 人物 + 作品 ----

def collect_history() -> dict[str, list[str]]:
    """Collect history entries from humanities.json."""
    hu = load_json("humanities.json")
    places = hu.get("places", {})
    result = {}
    for city, data in places.items():
        entries = []
        # 事件 (events)
        for ev in data.get("事件", []):
            text = ev.get("text", "").strip()
            if text:
                entries.append(text)
        # 人物 (people)
        for pe in data.get("人物", []):
            text = pe.get("text", "").strip()
            if text:
                entries.append(text)
        # 作品 (works)
        for wo in data.get("作品", []):
            text = wo.get("text", "").strip()
            if text:
                entries.append(text)
        if entries:
            result[city] = entries
    return result


# ---- 3. 地标: ask_kb.json -> keyword match to cities ----

LANDMARK_RE = re.compile(
    r"寺|庙|宫|塔|殿|楼|阁|桥|门|园|陵|窟|广场|城堡|教堂|宫殿"
)


def collect_landmarks() -> dict[str, list[str]]:
    """Collect landmark entries from ask_kb.json by matching city names + landmark keywords."""
    kb = load_json("ask_kb.json")
    lc = load_json("localcolor.json")
    hu = load_json("humanities.json")
    aliases = hu.get("aliases", {})

    # Build a set of all known city names (Chinese)
    known_cities = set(lc.keys())
    known_cities.update(hu.get("places", {}).keys())

    # Build reverse alias: Chinese -> list of English names
    cn_to_en = defaultdict(list)
    for en, cn in aliases.items():
        cn_to_en[cn].append(en)

    # For each KB entry, check if its name IS a known city -> city description
    # Also check if name contains a known city + landmark keyword
    result = defaultdict(list)

    for name, text in kb.items():
        if not text or not isinstance(text, str):
            continue

        # Direct city match: the KB entry name is a city
        if name in known_cities:
            result[name].append(text)
            continue

        # Check if name contains a known city name
        for city in known_cities:
            if city in name and city != name:
                # If the entry name contains a city and has landmark keywords
                if LANDMARK_RE.search(name):
                    result[city].append(f"{name}：{text}")
                    break

    return dict(result)


# ---- 4. 季节: seasonal.txt ----

def collect_seasonal() -> dict[str, list[str]]:
    """Collect seasonal entries from seasonal.txt."""
    lines = load_lines("seasonal.txt")
    result = defaultdict(list)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.match(r"\[(.+?)\|(.+?)\]\s*(.*)", line)
        if m:
            city, season, text = m.group(1), m.group(2), m.group(3)
            if text:
                result[city].append(f"[{season}] {text}")
    return dict(result)


# ---- 5. 节日: festivals.json ----

def collect_festivals() -> dict[str, list[str]]:
    """Collect festival entries from festivals.json."""
    fest = load_json("festivals.json")
    result = defaultdict(list)
    for entry in fest:
        place = entry.get("place", "").strip()
        if not place:
            continue
        name = entry.get("name", "")
        cards = entry.get("cards", [])
        eve_cards = entry.get("eve_cards", [])
        all_cards = cards + eve_cards
        if all_cards:
            for card in all_cards:
                result[place].append(f"[{name}] {card}")
        else:
            # Even without cards, note the festival exists
            result[place].append(f"[{name}]")
    return dict(result)


# ---- 6. 见闻: encounters.txt ----

def collect_encounters() -> dict[str, list[str]]:
    """Collect encounter entries from encounters.txt, mapping slugs to Chinese city names."""
    lines = load_lines("encounters.txt")
    result = defaultdict(list)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.match(r"\[(.+?)\]\s*(.*)", line)
        if not m:
            continue
        slug, text = m.group(1), m.group(2)
        if not text:
            continue

        # Map slug to Chinese city name
        city = SLUG_TO_CN.get(slug)
        if city:
            result[city].append(text)
        # Skip region/category slugs (africa, asia, art, polar, etc.)
    return dict(result)


# ---- Main aggregation ----

def build():
    print("Loading data sources...")

    food = collect_food()
    print(f"  美食 (localcolor): {len(food)} cities")

    history = collect_history()
    print(f"  历史 (humanities): {len(history)} cities")

    landmarks = collect_landmarks()
    print(f"  地标 (ask_kb):     {len(landmarks)} cities")

    seasonal = collect_seasonal()
    print(f"  季节 (seasonal):   {len(seasonal)} cities")

    festivals = collect_festivals()
    print(f"  节日 (festivals):  {len(festivals)} cities")

    encounters = collect_encounters()
    print(f"  见闻 (encounters): {len(encounters)} cities")

    # Merge all cities
    all_cities = set()
    for src in [food, history, landmarks, seasonal, festivals, encounters]:
        all_cities.update(src.keys())

    # Build output
    output = {}
    for city in sorted(all_cities):
        entry = {}
        for cat_name, src in [
            ("美食", food),
            ("历史", history),
            ("地标", landmarks),
            ("季节", seasonal),
            ("节日", festivals),
            ("见闻", encounters),
        ]:
            items = src.get(city, [])
            if items:
                entry[cat_name] = items
        if entry:
            output[city] = entry

    # Save
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Print statistics
    print(f"\n=== ask_city.json written to {OUT_PATH} ===")
    print(f"Total cities: {len(output)}")

    cat_counts = defaultdict(int)
    cat_entries = defaultdict(int)
    for city, cats in output.items():
        for cat, items in cats.items():
            cat_counts[cat] += 1
            cat_entries[cat] += len(items)

    print(f"\n{'类别':<8} {'城市数':>6} {'条目数':>8}")
    print("-" * 26)
    for cat in ["美食", "历史", "地标", "季节", "节日", "见闻"]:
        print(f"{cat:<8} {cat_counts[cat]:>6} {cat_entries[cat]:>8}")
    print(f"{'合计':<8} {len(output):>6} {sum(cat_entries.values()):>8}")


if __name__ == "__main__":
    build()
