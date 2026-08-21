"""Merge ecosystem data from batch JSON files into the main data files."""
import json, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "nowhere", "data")

FORBIDDEN = ["很", "非常", "十分", "巨大", "美丽", "一些", "很多", "感觉", "仿佛", "好像", "似乎", "有点"]

def check_forbidden(text, place, field):
    ok = True
    for w in FORBIDDEN:
        if w in text:
            print(f"  FORBIDDEN '{w}' in {place}/{field}: ...{text[:40]}...")
            ok = False
    return ok

def merge(batch_file):
    with open(batch_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Load existing
    with open(os.path.join(DATA, "localcolor.json"), "r", encoding="utf-8") as f:
        lc = json.load(f)
    with open(os.path.join(DATA, "humanities.json"), "r", encoding="utf-8") as f:
        hum = json.load(f)
    with open(os.path.join(DATA, "festivals.json"), "r", encoding="utf-8") as f:
        fes = json.load(f)
    with open(os.path.join(DATA, "souvenirs_by_place.json"), "r", encoding="utf-8") as f:
        souv = json.load(f)

    # Validate forbidden words
    print("=== Validating forbidden words ===")
    all_ok = True
    for place, cats in data.get("localcolor", {}).items():
        for cat, items in cats.items():
            for item in items:
                text = item if isinstance(item, str) else item.get("text", "")
                if not check_forbidden(text, place, f"lc/{cat}"):
                    all_ok = False
    for place, pdata in data.get("humanities", {}).items():
        for evt in pdata.get("事件", []):
            if not check_forbidden(evt["text"], place, "hum/event"):
                all_ok = False
        for per in pdata.get("人物", []):
            if not check_forbidden(per["text"], place, "hum/person"):
                all_ok = False
    for line in data.get("seasonal", []):
        text = line.split("] ", 1)[1] if "] " in line else ""
        place = line.split("|")[0][1:]
        if not check_forbidden(text, place, "seasonal"):
            all_ok = False
    for fest in data.get("festivals", []):
        for card in fest.get("cards", []) + fest.get("eve_cards", []):
            if not check_forbidden(card, fest["place"], "festival"):
                all_ok = False
    for place, items in data.get("souvenirs", {}).items():
        for item in items:
            if not check_forbidden(item["desc"], place, "souvenir"):
                all_ok = False

    if not all_ok:
        print("FAILED: forbidden words found. Fix before merging.")
        return False

    print("All cards passed forbidden word check!")

    # Merge localcolor
    print("\n=== Merging localcolor.json ===")
    for place, cats in data.get("localcolor", {}).items():
        if place not in lc:
            lc[place] = {}
        for cat, items in cats.items():
            if cat not in lc[place]:
                lc[place][cat] = []
            lc[place][cat].extend(items)
        total = sum(len(v) for v in lc[place].values() if isinstance(v, list))
        print(f"  {place}: {total} cards total")
    with open(os.path.join(DATA, "localcolor.json"), "w", encoding="utf-8") as f:
        json.dump(lc, f, ensure_ascii=False, indent=2)

    # Merge humanities
    print("\n=== Merging humanities.json ===")
    if "places" not in hum:
        hum["places"] = {}
    for place, pdata in data.get("humanities", {}).items():
        if place not in hum["places"]:
            hum["places"][place] = {}
        for evt in pdata.get("事件", []):
            if "事件" not in hum["places"][place]:
                hum["places"][place]["事件"] = []
            hum["places"][place]["事件"].append(evt)
        for per in pdata.get("人物", []):
            if "人物" not in hum["places"][place]:
                hum["places"][place]["人物"] = []
            hum["places"][place]["人物"].append(per)
        h = hum["places"][place]
        print(f"  {place}: {len(h.get('事件',[]))} events, {len(h.get('人物',[]))} people")
    with open(os.path.join(DATA, "humanities.json"), "w", encoding="utf-8") as f:
        json.dump(hum, f, ensure_ascii=False, indent=2)

    # Merge seasonal
    print("\n=== Merging seasonal.txt ===")
    seasonal_lines = data.get("seasonal", [])
    with open(os.path.join(DATA, "seasonal.txt"), "a", encoding="utf-8") as f:
        for line in seasonal_lines:
            f.write(line + "\n")
    print(f"  Added {len(seasonal_lines)} seasonal lines")

    # Merge festivals
    print("\n=== Merging festivals.json ===")
    fes.extend(data.get("festivals", []))
    with open(os.path.join(DATA, "festivals.json"), "w", encoding="utf-8") as f:
        json.dump(fes, f, ensure_ascii=False, indent=2)
    print(f"  Added {len(data.get('festivals', []))} festivals")

    # Merge souvenirs
    print("\n=== Merging souvenirs_by_place.json ===")
    for place, items in data.get("souvenirs", {}).items():
        if place not in souv:
            souv[place] = []
        souv[place].extend(items)
        print(f"  {place}: {len(items)} souvenirs")
    with open(os.path.join(DATA, "souvenirs_by_place.json"), "w", encoding="utf-8") as f:
        json.dump(souv, f, ensure_ascii=False, indent=2)

    print("\n=== Merge complete! ===")
    lc_places = len(data.get("localcolor", {}))
    lc_cards = sum(sum(len(v) for v in cats.values()) for cats in data.get("localcolor", {}).values())
    print(f"Places: {lc_places}")
    print(f"Localcolor cards: {lc_cards}")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python merge_ecosystem.py <batch_data.json>")
        sys.exit(1)
    success = merge(sys.argv[1])
    sys.exit(0 if success else 1)
