#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build explorable_index.json for Nowhere travel app.
Scans all content files and builds a unified index mapping places to content layers.
"""

import json
import re
import os
from collections import defaultdict

DATA_DIR = os.path.dirname(os.path.abspath(__file__))

def load_json(filename):
    """Load a JSON file with UTF-8 encoding."""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def extract_places_from_txt(filename):
    """Extract place names from text files with [place] or [place|season] format."""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return set()
    places = set()
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            # Match [place] or [place|season] or [place|other]
            matches = re.findall(r'\[([^\]|]+)(?:\|[^\]]+)?\]', line)
            for m in matches:
                m = m.strip()
                if m and not m.startswith('#') and len(m) < 30:
                    places.add(m)
    return places

def extract_places_from_scene_txt(filename):
    """Extract place names from scene files that use [place] format."""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return set()
    places = set()
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            # Skip comment lines
            if line.startswith('#') or line.startswith('##'):
                continue
            # Match [place] at start of line or after whitespace
            matches = re.findall(r'^\[([^\]]+)\]', line.strip())
            for m in matches:
                m = m.strip()
                # Filter out non-place entries (like phenomena descriptions)
                if m and not any(c in m for c in '，。、；：') and len(m) < 20:
                    places.add(m)
    return places

def extract_places_from_scene_world(filename):
    """Extract place names from scene_world_enhanced.txt (uses 'place description' format)."""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return set()
    places = set()
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Format: "东京 描述" - place name followed by space and description
            parts = line.split(' ', 1)
            if len(parts) >= 2:
                place = parts[0].strip()
                # Filter: must be Chinese characters, reasonable length
                if place and re.match(r'^[一-鿿]+$', place) and len(place) <= 10:
                    places.add(place)
    return places

def build_coordinates():
    """Build a coordinate lookup from all available sources."""
    coords = {}

    # From pool.json (list of places with lat/lon)
    pool = load_json('pool.json')
    if isinstance(pool, list):
        for item in pool:
            name = item.get('name_hint', '')
            if name and 'lat' in item and 'lon' in item:
                coords[name] = {'lat': item['lat'], 'lon': item['lon']}

    # From places_patch.json
    patch = load_json('places_patch.json')
    if isinstance(patch, dict):
        for name, info in patch.items():
            if isinstance(info, dict) and 'lat' in info and 'lon' in info:
                coords[name] = {'lat': info['lat'], 'lon': info['lon']}

    # From special_places.json
    special = load_json('special_places.json')
    if isinstance(special, dict):
        for name, info in special.items():
            if isinstance(info, dict) and 'lat' in info and 'lon' in info:
                coords[name] = {'lat': info['lat'], 'lon': info['lon']}

    # From humanities.json places (some have lat/lon)
    humanities = load_json('humanities.json')
    if isinstance(humanities, dict):
        places = humanities.get('places', {})
        for name, info in places.items():
            if isinstance(info, dict) and 'lat' in info and 'lon' in info:
                coords[name] = {'lat': info['lat'], 'lon': info['lon']}

    # From water_features_offline.json
    water_offline = load_json('water_features_offline.json')
    if isinstance(water_offline, dict):
        entries = water_offline.get('entries', [])
        for item in entries:
            name = item.get('name', '')
            if name and 'lat' in item and 'lon' in item:
                coords[name] = {'lat': item['lat'], 'lon': item['lon']}

    return coords

def build_encounters_index():
    """Build index from encounters.txt.
    encounters.txt uses regional/biome tags like [polar], [asia], [europe], etc.
    We'll index these as regions, not cities.
    """
    path = os.path.join(DATA_DIR, 'encounters.txt')
    if not os.path.exists(path):
        return {}
    # Count entries per region/biome
    region_counts = defaultdict(int)
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            matches = re.findall(r'\[([^\]|]+)\]', line)
            for m in matches:
                m = m.strip()
                if m:
                    region_counts[m] += 1
    return dict(region_counts)

def build_scene_index():
    """Build index from all scene_*.txt files."""
    scene_places = defaultdict(set)

    # scene_soundscape.txt - [place] format
    places = extract_places_from_scene_txt('scene_soundscape.txt')
    for p in places:
        scene_places[p].add('soundscape')

    # scene_taste.txt - [place] format
    places = extract_places_from_scene_txt('scene_taste.txt')
    for p in places:
        scene_places[p].add('taste')

    # scene_touch.txt - no place tags, generic
    # scene_food.txt - no place tags, generic
    # scene_life.txt - no place tags, generic
    # scene_plants.txt - no place tags, generic
    # scene_sky.txt - no place tags, generic
    # scene_museum.txt - no place tags, generic
    # scene_walk_discovery.txt - no place tags, generic
    # scene_water_features.txt - no place tags, generic

    # scene_china_enhanced.txt - [place] format
    places = extract_places_from_scene_txt('scene_china_enhanced.txt')
    for p in places:
        scene_places[p].add('china_enhanced')

    # scene_ocean.txt - [phenomenon] format, not place-based
    # scene_phenomena.txt - [phenomenon] format, not place-based

    # scene_world_enhanced.txt - "place description" format
    places = extract_places_from_scene_world('scene_world_enhanced.txt')
    for p in places:
        scene_places[p].add('world_enhanced')

    return scene_places

def build_seasonal_index():
    """Build index from seasonal_*.txt files."""
    seasonal_places = set()

    # Main seasonal.txt - [place|season] format
    path = os.path.join(DATA_DIR, 'seasonal.txt')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                matches = re.findall(r'\[([^\]|]+)\|[^\]]+\]', line)
                for m in matches:
                    m = m.strip()
                    if m and len(m) < 20:
                        seasonal_places.add(m)

    # seasonal_americas_africa_oceania.txt
    path = os.path.join(DATA_DIR, 'seasonal_americas_africa_oceania.txt')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                matches = re.findall(r'\[([^\]|]+)\|[^\]]+\]', line)
                for m in matches:
                    m = m.strip()
                    if m and len(m) < 20:
                        seasonal_places.add(m)

    # seasonal_east_asia.txt
    path = os.path.join(DATA_DIR, 'seasonal_east_asia.txt')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                matches = re.findall(r'\[([^\]|]+)\|[^\]]+\]', line)
                for m in matches:
                    m = m.strip()
                    if m and len(m) < 20:
                        seasonal_places.add(m)

    # seasonal_europe.txt
    path = os.path.join(DATA_DIR, 'seasonal_europe.txt')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                matches = re.findall(r'\[([^\]|]+)\|[^\]]+\]', line)
                for m in matches:
                    m = m.strip()
                    if m and len(m) < 20:
                        seasonal_places.add(m)

    # seasonal_biomes_cold.txt - [biome|season] format
    # seasonal_forests.txt - [biome|season] format
    # seasonal_mountains_coast.txt - [biome|season] format
    # seasonal_natural.txt - [biome|season] format
    # These are biome-based, not city-based

    return seasonal_places

def build_localcolor_index():
    """Build index from localcolor*.json files."""
    localcolor_places = {}

    # Main localcolor.json
    data = load_json('localcolor.json')
    if isinstance(data, dict):
        for place in data.keys():
            localcolor_places[place] = True

    # Regional files
    for fname in ['localcolor_china.json', 'localcolor_europe_middleeast.json',
                   'localcolor_japan_korea_sea.json', 'localcolor_americas_africa_oceania.json',
                   'localcolor_natural.json']:
        data = load_json(fname)
        if isinstance(data, dict):
            for place in data.keys():
                localcolor_places[place] = True

    return localcolor_places

def build_humanities_index():
    """Build index from humanities.json."""
    humanities = load_json('humanities.json')
    if not isinstance(humanities, dict):
        return {}

    places_data = humanities.get('places', {})
    result = {}
    for place, info in places_data.items():
        if isinstance(info, dict):
            counts = {}
            if '事件' in info:
                counts['events'] = len(info['事件'])
            if '人物' in info:
                counts['people'] = len(info['人物'])
            if '作品' in info:
                counts['works'] = len(info['作品'])
            if counts:
                result[place] = counts

    return result

def build_knowledge_index():
    """Build index from knowledge.json."""
    knowledge = load_json('knowledge.json')
    if not isinstance(knowledge, dict):
        return {}
    # knowledge.json is organized by country, not by city
    # We'll mark countries as having knowledge
    return {k: True for k in knowledge.keys() if not k.startswith('_')}

def build_films_index():
    """Build index from humanities_films.json."""
    films = load_json('humanities_films.json')
    if not isinstance(films, dict):
        return {}
    result = {}
    for place, info in films.items():
        if isinstance(info, dict) and '作品' in info:
            result[place] = len(info['作品'])
    return result

def build_historical_index():
    """Build index from humanities_historical.json."""
    historical = load_json('humanities_historical.json')
    if not isinstance(historical, dict):
        return {}
    result = {}
    for place, info in historical.items():
        if place.startswith('_'):
            continue
        if isinstance(info, dict) and '人物' in info:
            result[place] = len(info['人物'])
        elif isinstance(info, list):
            result[place] = len(info)
    return result

def build_water_index():
    """Build index from water_features_scenes.json."""
    water = load_json('water_features_scenes.json')
    if not isinstance(water, dict):
        return {}
    # Map English city names to Chinese
    en_to_zh = {
        'Minneapolis': '明尼阿波利斯', 'St. Louis': '圣路易斯', 'Memphis': '孟菲斯',
        'New Orleans': '新奥尔良', 'Vienna': '维也纳', 'Budapest': '布达佩斯',
        'Belgrade': '贝尔格莱德', 'Bucharest': '布加勒斯特', 'Basel': '巴塞尔',
        'Strasbourg': '斯特拉斯堡', 'Cologne': '科隆', 'Rotterdam': '鹿特丹',
        'Cairo': '开罗', 'Aswan': '阿斯旺', 'Khartoum': '喀土穆',
        'Manaus': '玛瑙斯', 'Belem': '贝伦', 'London': '伦敦', 'Oxford': '牛津',
        'Varanasi': '瓦拉纳西', 'Kolkata': '加尔各答', 'Vientiane': '万象',
        'Phnom Penh': '金边', 'Ho Chi Minh': '胡志明市', 'Moscow': '莫斯科',
        'Kazan': '喀山', 'Volgograd': '伏尔加格勒', 'Melbourne': '墨尔本',
        'Sydney': '悉尼',
    }
    # Map English water feature names to Chinese
    water_en_to_zh = {
        'Mississippi': '密西西比河', 'Danube': '多瑙河', 'Rhine': '莱茵河',
        'Nile': '尼罗河', 'Amazon': '亚马逊河', 'Thames': '泰晤士河',
        'Ganges': '恒河', 'Mekong': '湄公河', 'Volga': '伏尔加河',
        'Murray': '墨累河',
    }
    # Map water features to nearby cities
    result = {}
    for water_name, info in water.items():
        if isinstance(info, dict) and 'segments' in info:
            # Get Chinese water name
            water_zh = water_en_to_zh.get(water_name, water_name)
            for segment_name, seg_info in info['segments'].items():
                # Extract city name from segment name
                city = segment_name.replace('段', '').replace('流域', '').strip()
                # Map English names to Chinese
                city = en_to_zh.get(city, city)
                if city:
                    result[city] = water_zh
    return result

def build_art_index():
    """Build index from art_by_city.json."""
    art = load_json('art_by_city.json')
    if not isinstance(art, dict):
        return {}
    result = {}
    for place, info in art.items():
        if isinstance(info, dict) and 'artworks' in info:
            result[place] = len(info['artworks'])
    return result

def build_flora_index():
    """Build index from flora_by_place.json."""
    flora = load_json('flora_by_place.json')
    if not isinstance(flora, dict):
        return {}
    return {k: True for k in flora.keys()}

def build_food_index():
    """Build index from food_by_country.json."""
    food = load_json('food_by_country.json')
    if not isinstance(food, dict):
        return {}
    # Map ISO country codes to Chinese country names
    code_to_name = {
        'HU': '匈牙利', 'CA': '加拿大', 'FI': '芬兰', 'GH': '加纳',
        'KE': '肯尼亚', 'IS': '冰岛', 'UA': '乌克兰', 'NO': '挪威',
        'EE': '爱沙尼亚', 'IE': '爱尔兰', 'LU': '卢森堡', 'DK': '丹麦',
        'JP': '日本', 'IL': '以色列', 'AZ': '阿塞拜疆', 'HR': '克罗地亚',
        'PY': '巴拉圭', 'HT': '海地', 'MK': '北马其顿', 'KH': '柬埔寨',
        'BO': '玻利维亚', 'BA': '波黑', 'GE': '格鲁吉亚', 'AM': '亚美尼亚',
        'MD': '摩尔多瓦', 'ME': '黑山', 'AL': '阿尔巴尼亚', 'RS': '塞尔维亚',
        'MT': '马耳他', 'CY': '塞浦路斯', 'LV': '拉脱维亚', 'LT': '立陶宛',
        'SK': '斯洛伐克', 'SI': '斯洛文尼亚', 'BG': '保加利亚', 'RO': '罗马尼亚',
        'CZ': '捷克', 'PL': '波兰', 'HU': '匈牙利', 'AT': '奥地利',
        'CH': '瑞士', 'BE': '比利时', 'NL': '荷兰', 'SE': '瑞典',
        'PT': '葡萄牙', 'GR': '希腊', 'ES': '西班牙', 'IT': '意大利',
        'DE': '德国', 'FR': '法国', 'GB': '英国', 'US': '美国',
        'MX': '墨西哥', 'BR': '巴西', 'AR': '阿根廷', 'CL': '智利',
        'CO': '哥伦比亚', 'PE': '秘鲁', 'VE': '委内瑞拉', 'EC': '厄瓜多尔',
        'UY': '乌拉圭', 'PA': '巴拿马', 'CR': '哥斯达黎加', 'CU': '古牙',
        'JM': '牙买加', 'TT': '特立尼达和多巴哥', 'DO': '多米尼加',
        'GT': '危地马拉', 'HN': '洪都拉斯', 'SV': '萨尔瓦多', 'NI': '尼加拉瓜',
        'CN': '中国', 'KR': '韩国', 'TH': '泰国', 'VN': '越南',
        'MY': '马来西亚', 'SG': '新加坡', 'ID': '印度尼西亚', 'PH': '菲律宾',
        'IN': '印度', 'PK': '巴基斯坦', 'BD': '孟加拉国', 'LK': '斯里兰卡',
        'NP': '尼泊尔', 'MM': '缅甸', 'LA': '老挝', 'KH': '柬埔寨',
        'MN': '蒙古', 'KZ': '哈萨克斯坦', 'UZ': '乌兹别克斯坦',
        'TM': '土库曼斯坦', 'KG': '吉尔吉斯斯坦', 'TJ': '塔吉克斯坦',
        'AF': '阿富汗', 'IR': '伊朗', 'IQ': '伊拉克', 'SA': '沙特阿拉伯',
        'AE': '阿联酋', 'QA': '卡塔尔', 'BH': '巴林', 'KW': '科威特',
        'OM': '阿曼', 'YE': '也门', 'JO': '约旦', 'LB': '黎巴嫩',
        'SY': '叙利亚', 'TR': '土耳其', 'EG': '埃及', 'LY': '利比亚',
        'TN': '突尼斯', 'DZ': '阿尔及利亚', 'MA': '摩洛哥', 'SD': '苏丹',
        'ET': '埃塞俄比亚', 'SO': '索马里', 'DJ': '吉布提', 'ER': '厄立特里亚',
        'UG': '乌干达', 'RW': '卢旺达', 'BI': '布隆迪', 'TZ': '坦桑尼亚',
        'MZ': '莫桑比克', 'ZW': '津巴布韦', 'ZM': '赞比亚', 'MW': '马拉维',
        'BW': '博茨瓦纳', 'NA': '纳米比亚', 'ZA': '南非', 'MG': '马达加斯加',
        'MU': '毛里求斯', 'SC': '塞舌尔', 'NG': '尼日利亚', 'GH': '加纳',
        'CI': '科特迪瓦', 'SN': '塞内加尔', 'ML': '马里', 'BF': '布基纳法索',
        'NE': '尼日尔', 'TD': '乍得', 'CM': '喀麦隆', 'GA': '加蓬',
        'CG': '刚果(布)', 'CD': '刚果(金)', 'AO': '安哥拉', 'MZ': '莫桑比克',
        'AU': '澳大利亚', 'NZ': '新西兰', 'FJ': '斐济', 'PG': '巴布亚新几内亚',
        'WS': '萨摩亚', 'TO': '汤加', 'VU': '瓦努阿图', 'SB': '所罗门群岛',
        'RU': '俄罗斯', 'UA': '乌克兰', 'BY': '白俄罗斯', 'MD': '摩尔多瓦',
        'GE': '格鲁吉亚', 'AM': '亚美尼亚', 'AZ': '阿塞拜疆',
    }
    result = {}
    for code in food.keys():
        name = code_to_name.get(code, code)
        result[name] = True
    return result

def build_souvenirs_index():
    """Build index from souvenirs_by_place.json."""
    souvenirs = load_json('souvenirs_by_place.json')
    if not isinstance(souvenirs, dict):
        return {}
    return {k: True for k in souvenirs.keys()}

def main():
    print("Building explorable index...")

    # Build all indices
    coords = build_coordinates()
    print(f"  Coordinates loaded: {len(coords)} places")

    encounters = build_encounters_index()
    print(f"  Encounters: {len(encounters)} places")

    scene = build_scene_index()
    print(f"  Scene: {len(scene)} places")

    seasonal = build_seasonal_index()
    print(f"  Seasonal: {len(seasonal)} places")

    localcolor = build_localcolor_index()
    print(f"  Localcolor: {len(localcolor)} places")

    humanities = build_humanities_index()
    print(f"  Humanities: {len(humanities)} places")

    knowledge = build_knowledge_index()
    print(f"  Knowledge: {len(knowledge)} places")

    films = build_films_index()
    print(f"  Films: {len(films)} places")

    historical = build_historical_index()
    print(f"  Historical: {len(historical)} places")

    water = build_water_index()
    print(f"  Water: {len(water)} places")

    art = build_art_index()
    print(f"  Art: {len(art)} places")

    flora = build_flora_index()
    print(f"  Flora: {len(flora)} places")

    food = build_food_index()
    print(f"  Food: {len(food)} places")

    souvenirs = build_souvenirs_index()
    print(f"  Souvenirs: {len(souvenirs)} places")

    # Collect all unique places
    all_places = set()
    all_places.update(encounters.keys())
    all_places.update(scene.keys())
    all_places.update(seasonal)
    all_places.update(localcolor.keys())
    all_places.update(humanities.keys())
    all_places.update(films.keys())
    all_places.update(historical.keys())
    all_places.update(water.keys())
    all_places.update(art.keys())
    all_places.update(flora.keys())
    all_places.update(souvenirs.keys())
    # Also add places from pool.json
    for name in coords.keys():
        all_places.add(name)

    print(f"\n  Total unique places: {len(all_places)}")

    # Build the unified index
    places_index = {}
    for place in sorted(all_places):
        entry = {}

        # Add coordinates if available
        if place in coords:
            entry['lat'] = coords[place]['lat']
            entry['lon'] = coords[place]['lon']

        # Build layers
        layers = {}

        # Encounters (regions/biomes with entry counts)
        if place in encounters:
            layers['encounters'] = encounters[place]

        # Scene
        if place in scene:
            layers['scene'] = sorted(list(scene[place]))

        # Seasonal
        if place in seasonal:
            layers['seasonal'] = True

        # Localcolor
        if place in localcolor:
            layers['localcolor'] = True

        # Humanities
        if place in humanities:
            layers['humanities'] = humanities[place]

        # Knowledge
        if place in knowledge:
            layers['knowledge'] = True

        # Films
        if place in films:
            layers['films'] = films[place]

        # Historical
        if place in historical:
            layers['historical'] = historical[place]

        # Water
        if place in water:
            layers['water'] = water[place]

        # Art
        if place in art:
            layers['art'] = art[place]

        # Flora
        if place in flora:
            layers['flora'] = True

        # Food (by country code)
        if place in food:
            layers['food'] = True

        # Souvenirs
        if place in souvenirs:
            layers['souvenirs'] = True

        if layers:
            entry['layers'] = layers
            places_index[place] = entry

    # Build stats
    stats = {
        'total_places': len(places_index),
        'with_coordinates': sum(1 for p in places_index.values() if 'lat' in p),
        'by_layer': {
            'encounters': sum(1 for p in places_index.values() if p.get('layers', {}).get('encounters')),
            'scene': sum(1 for p in places_index.values() if p.get('layers', {}).get('scene')),
            'seasonal': sum(1 for p in places_index.values() if p.get('layers', {}).get('seasonal')),
            'localcolor': sum(1 for p in places_index.values() if p.get('layers', {}).get('localcolor')),
            'humanities': sum(1 for p in places_index.values() if p.get('layers', {}).get('humanities')),
            'knowledge': sum(1 for p in places_index.values() if p.get('layers', {}).get('knowledge')),
            'films': sum(1 for p in places_index.values() if p.get('layers', {}).get('films')),
            'historical': sum(1 for p in places_index.values() if p.get('layers', {}).get('historical')),
            'water': sum(1 for p in places_index.values() if p.get('layers', {}).get('water')),
            'art': sum(1 for p in places_index.values() if p.get('layers', {}).get('art')),
            'flora': sum(1 for p in places_index.values() if p.get('layers', {}).get('flora')),
            'food': sum(1 for p in places_index.values() if p.get('layers', {}).get('food')),
            'souvenirs': sum(1 for p in places_index.values() if p.get('layers', {}).get('souvenirs')),
        }
    }

    # Build final output
    output = {
        'places': places_index,
        'stats': stats
    }

    # Save
    output_path = os.path.join(DATA_DIR, 'explorable_index.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  Saved to: {output_path}")
    print(f"  Total places indexed: {stats['total_places']}")
    print(f"  Places with coordinates: {stats['with_coordinates']}")
    print(f"\n  Layer coverage:")
    for layer, count in stats['by_layer'].items():
        print(f"    {layer}: {count}")

if __name__ == '__main__':
    main()
