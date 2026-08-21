#!/usr/bin/env python3
"""Batch 0: Alias normalization + scattered data collection.

Merges alias keys into canonical keys across all data files.
Adds humanities entries missing from explorable_index.
"""
import json
import os

DATA = os.path.join(os.path.dirname(__file__), '..', 'nowhere', 'data')

def load(name):
    with open(os.path.join(DATA, name), 'r', encoding='utf-8') as f:
        return json.load(f)

def save(name, data):
    with open(os.path.join(DATA, name), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Alias map: alias -> canonical
ALIAS_MAP = {
    '杜拜': '迪拜',
    '列宁格勒': '圣彼得堡',
    '莫尔斯比港': '莫尔兹比港',
    '德国格尔利茨': '格尔利茨',
    '法国普罗旺斯': '普罗旺斯',
    '突尼斯': '突尼斯城',
    '西西里': '西西里岛',
    '克罗地亚海岸': '克罗地亚',
    '阿塔卡马沙漠': '阿塔卡马',
}

def merge_explorable_index(data):
    places = data.get('places', data)
    changes = []
    for alias, canon in ALIAS_MAP.items():
        if alias not in places:
            continue
        if canon not in places:
            places[canon] = places.pop(alias)
            changes.append(f"  renamed '{alias}' -> '{canon}'")
            continue
        a_entry = places[alias]
        c_entry = places[canon]
        a_layers = a_entry.get('layers', {})
        c_layers = c_entry.get('layers', {})
        for layer, a_val in a_layers.items():
            if layer not in c_layers:
                c_layers[layer] = a_val
            elif isinstance(a_val, list) and isinstance(c_layers[layer], list):
                for item in a_val:
                    if item not in c_layers[layer]:
                        c_layers[layer].append(item)
            elif isinstance(a_val, dict) and isinstance(c_layers[layer], dict):
                for k, v in a_val.items():
                    if k not in c_layers[layer]:
                        c_layers[layer][k] = v
                    elif isinstance(v, (int, float)) and isinstance(c_layers[layer][k], (int, float)):
                        c_layers[layer][k] = max(v, c_layers[layer][k])
        if 'lat' not in c_entry and 'lat' in a_entry:
            c_entry['lat'] = a_entry['lat']
        if 'lon' not in c_entry and 'lon' in a_entry:
            c_entry['lon'] = a_entry['lon']
        c_entry['layers'] = c_layers
        del places[alias]
        changes.append(f"  merged '{alias}' -> '{canon}' (layers: {list(a_layers.keys())})")
    return changes

def add_humanities_to_index(data):
    places = data.get('places', data)
    h = load('humanities.json')
    h_places = h.get('places', {})
    added = []
    for name, entry in h_places.items():
        if name in places:
            continue
        if not isinstance(entry, dict):
            continue
        has_content = any(k in entry and entry[k] for k in ['事件', '人物', 'works', 'cards'])
        if not has_content:
            continue
        new_entry = {'layers': {'humanities': True}}
        if 'lat' in entry:
            new_entry['lat'] = entry['lat']
        if 'lon' in entry:
            new_entry['lon'] = entry['lon']
        places[name] = new_entry
        added.append(name)
    return added

def merge_humanities(data):
    changes = []
    h_places = data.get('places', {})
    h_aliases = data.get('aliases', {})
    for alias, canon in ALIAS_MAP.items():
        if alias in h_places:
            a_val = h_places[alias]
            if canon in h_places:
                c_val = h_places[canon]
                if isinstance(a_val, dict) and isinstance(c_val, dict):
                    for k, v in a_val.items():
                        if k in ('lat', 'lon'):
                            if k not in c_val:
                                c_val[k] = v
                        elif k not in c_val:
                            c_val[k] = v
                        elif isinstance(v, list) and isinstance(c_val[k], list):
                            for item in v:
                                if item not in c_val[k]:
                                    c_val[k].append(item)
                del h_places[alias]
                changes.append(f"  merged places '{alias}' -> '{canon}'")
            else:
                h_places[canon] = h_places.pop(alias)
                changes.append(f"  renamed places '{alias}' -> '{canon}'")
    for eng, zh in list(h_aliases.items()):
        if zh in ALIAS_MAP:
            h_aliases[eng] = ALIAS_MAP[zh]
            changes.append(f"  updated alias ref {eng}: '{zh}' -> '{ALIAS_MAP[zh]}'")
    return changes

def merge_generic_dict(data, fname):
    changes = []
    for alias, canon in ALIAS_MAP.items():
        if alias not in data:
            continue
        if canon not in data:
            data[canon] = data.pop(alias)
            changes.append(f"  renamed '{alias}' -> '{canon}'")
            continue
        a_val = data[alias]
        c_val = data[canon]
        if isinstance(a_val, list) and isinstance(c_val, list):
            for item in a_val:
                if item not in c_val:
                    c_val.append(item)
        elif isinstance(a_val, dict) and isinstance(c_val, dict):
            for k, v in a_val.items():
                if k not in c_val:
                    c_val[k] = v
        del data[alias]
        changes.append(f"  merged '{alias}' -> '{canon}'")
    return changes

def main():
    report = []
    report.append("=" * 60)
    report.append("Batch 0: Alias Normalization Report")
    report.append("=" * 60)

    # 1. humanities.json - merge aliases FIRST
    h = load('humanities.json')
    changes = merge_humanities(h)
    if changes:
        save('humanities.json', h)
        report.append(f"\nhumanities.json:")
        report.extend(changes)

    # 2. explorable_index.json - merge aliases
    idx = load('explorable_index.json')
    changes = merge_explorable_index(idx)
    if changes:
        save('explorable_index.json', idx)
        report.append(f"\nexplorable_index.json (alias merge):")
        report.extend(changes)

    # 3. explorable_index.json - add missing humanities entries
    idx = load('explorable_index.json')
    added = add_humanities_to_index(idx)
    if added:
        save('explorable_index.json', idx)
        report.append(f"\nexplorable_index.json (added {len(added)} humanities entries):")
        for name in added:
            report.append(f"  + {name}")

    # 4. Other dict-keyed files
    other_files = [
        'localcolor.json', 'flora_by_place.json',
        'humanities_films.json', 'humanities_historical.json',
        'art_by_city.json', 'food_by_country.json',
        'localcolor_china.json', 'localcolor_europe_middleeast.json',
        'localcolor_americas_africa_oceania.json',
        'localcolor_japan_korea_sea.json', 'localcolor_natural.json',
    ]
    for fname in other_files:
        fpath = os.path.join(DATA, fname)
        if not os.path.exists(fpath):
            continue
        data = load(fname)
        if not isinstance(data, dict):
            continue
        changes = merge_generic_dict(data, fname)
        if changes:
            save(fname, data)
            report.append(f"\n{fname}:")
            report.extend(changes)

    # 5. festivals.json (list)
    fest = load('festivals.json')
    if isinstance(fest, list):
        changes = []
        for item in fest:
            place = item.get('place', '')
            if place in ALIAS_MAP:
                old = place
                item['place'] = ALIAS_MAP[place]
                changes.append(f"  '{old}' -> '{ALIAS_MAP[old]}' in '{item.get('name','')}'")
        if changes:
            save('festivals.json', fest)
            report.append(f"\nfestivals.json:")
            report.extend(changes)

    # Verification
    report.append("")
    report.append("=" * 60)
    idx = load('explorable_index.json')
    places = idx['places']
    report.append(f"explorable_index places: {len(places)}")
    remaining = [a for a in ALIAS_MAP if a in places]
    if remaining:
        report.append(f"WARNING: alias keys still present: {remaining}")
    else:
        report.append("All alias keys resolved.")
    print('\n'.join(report))

if __name__ == '__main__':
    main()
