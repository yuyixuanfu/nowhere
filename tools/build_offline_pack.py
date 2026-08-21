"""Build the offline data pack for nowhere.

Extracts curated subsets of large data files for the ~290MB offline package.

Usage:
    python tools/build_offline_pack.py

Output: nowhere/data/offline/ directory with curated data files.
"""
import gzip
import json
import pathlib
import sys
import io

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DATA = pathlib.Path(__file__).resolve().parent.parent / "nowhere" / "data"
OUT = DATA / "offline"
OUT.mkdir(exist_ok=True)


def build_zim_subset():
    """Extract key articles from the full ZIM into a smaller subset."""
    zim_path = DATA / "packs" / "wikipedia_zh_mini.zim"
    if not zim_path.exists():
        print("  ⚠️ ZIM file not found, skipping")
        return

    print("  Loading ZIM (this may take a moment)...")
    try:
        from zimply.zimply import ZIMFile
        zim = ZIMFile(str(zim_path), encoding="utf-8")
    except Exception as e:
        print(f"  ⚠️ Failed to load ZIM: {e}")
        return

    # Collect article titles to extract
    titles = set()

    # 1. All knowledge base entries
    for fname in ["knowledge_asia.json", "knowledge_europe.json",
                  "knowledge_americas_africa_oceania.json"]:
        fp = DATA / fname
        if fp.exists():
            kb = json.loads(fp.read_text(encoding="utf-8"))
            for place in kb.keys():
                titles.add(place)

    # 2. All humanities places
    hfp = DATA / "humanities.json"
    if hfp.exists():
        hdata = json.loads(hfp.read_text(encoding="utf-8"))
        for place in hdata.get("places", {}).keys():
            titles.add(place)

    # 3. Major world landmarks
    landmarks = [
        "富士山", "珠穆朗玛峰", "万里长城", "故宫", "金字塔",
        "大堡礁", "亚马逊雨林", "撒哈拉沙漠", "北极", "南极",
        "马丘比丘", "吴哥窟", "泰姬陵", "自由女神像", "埃菲尔铁塔",
        "大堡礁", "死海", "贝加尔湖", "维多利亚瀑布", "大峡谷",
        "乞力马扎罗山", "阿尔卑斯山", "喜马拉雅山脉", "安第斯山脉",
        "长江", "黄河", "亚马逊河", "尼罗河", "密西西比河",
        "地中海", "黑海", "红海", "波斯湾", "孟加拉湾",
    ]
    titles.update(landmarks)

    # 4. Country names (all UN member states + major territories)
    countries = [
        "中国", "日本", "韩国", "朝鲜", "蒙古", "印度", "巴基斯坦", "孟加拉国",
        "斯里兰卡", "尼泊尔", "不丹", "缅甸", "泰国", "越南", "柬埔寨", "老挝",
        "马来西亚", "新加坡", "印度尼西亚", "菲律宾", "文莱", "东帝汶",
        "哈萨克斯坦", "乌兹别克斯坦", "土库曼斯坦", "塔吉克斯坦", "吉尔吉斯斯坦",
        "伊朗", "伊拉克", "土耳其", "叙利亚", "约旦", "以色列", "黎巴嫩",
        "沙特阿拉伯", "也门", "阿曼", "阿联酋", "卡塔尔", "巴林", "科威特", "阿富汗",
        "法国", "德国", "意大利", "西班牙", "葡萄牙", "英国", "爱尔兰",
        "荷兰", "比利时", "卢森堡", "瑞士", "奥地利", "波兰", "捷克", "斯洛伐克",
        "匈牙利", "罗马尼亚", "保加利亚", "希腊", "塞尔维亚", "克罗地亚",
        "波黑", "黑山", "北马其顿", "阿尔巴尼亚", "斯洛文尼亚",
        "挪威", "瑞典", "芬兰", "丹麦", "冰岛", "爱沙尼亚", "拉脱维亚", "立陶宛",
        "乌克兰", "白俄罗斯", "摩尔多瓦", "俄罗斯", "格鲁吉亚", "亚美尼亚", "阿塞拜疆",
        "埃及", "利比亚", "突尼斯", "阿尔及利亚", "摩洛哥", "苏丹", "南苏丹",
        "埃塞俄比亚", "肯尼亚", "坦桑尼亚", "乌干达", "卢旺达", "刚果(金)", "刚果(布)",
        "喀麦隆", "尼日利亚", "加纳", "科特迪瓦", "塞内加尔", "马里", "尼日尔",
        "乍得", "中非", "纳米比亚", "博茨瓦纳", "津巴布韦", "莫桑比克", "马达加斯加", "南非",
        "加拿大", "美国", "墨西哥", "危地马拉", "洪都拉斯", "尼加拉瓜", "哥斯达黎加", "巴拿马",
        "古巴", "牙买加", "海地", "多米尼加", "哥伦比亚", "委内瑞拉", "厄瓜多尔",
        "秘鲁", "巴西", "玻利维亚", "智利", "阿根廷", "巴拉圭", "乌拉圭", "圭亚那",
        "澳大利亚", "新西兰", "巴布亚新几内亚", "斐济", "萨摩亚", "汤加",
    ]
    titles.update(countries)

    # 5. Major cities worldwide
    extra_cities = [
        "北京", "上海", "重庆", "成都", "西安", "拉萨", "哈尔滨", "大理", "喀什", "敦煌",
        "南京", "杭州", "苏州", "广州", "深圳", "天津", "武汉", "长沙", "昆明", "贵阳",
        "东京", "京都", "大阪", "名古屋", "福冈", "札幌", "那霸",
        "首尔", "釜山", "平壤", "乌兰巴托",
        "曼谷", "清迈", "河内", "胡志明市", "雅加达", "巴厘岛", "马尼拉", "仰光", "金边", "万象",
        "新加坡", "吉隆坡", "槟城",
        "孟买", "新德里", "加尔各答", "班加罗尔", "加德满都", "科伦坡", "达卡", "伊斯兰堡", "卡拉奇",
        "伊斯坦布尔", "安卡拉", "德黑兰", "伊斯法罕", "巴格达", "利雅得", "迪拜", "多哈", "安曼",
        "莫斯科", "圣彼得堡", "基辅", "明斯克",
        "柏林", "慕尼黑", "汉堡", "巴黎", "里昂", "伦敦", "曼彻斯特", "爱丁堡",
        "罗马", "米兰", "威尼斯", "佛罗伦萨", "那不勒斯",
        "马德里", "巴塞罗那", "里斯本", "波尔图",
        "阿姆斯特丹", "布鲁塞尔", "维也纳", "布拉格", "布达佩斯",
        "华沙", "布加勒斯特", "雅典", "赫尔辛基", "斯德哥尔摩", "奥斯陆", "哥本哈根", "雷克雅未克",
        "开罗", "开普敦", "约翰内斯堡", "内罗毕", "达累斯萨拉姆", "亚的斯亚贝巴", "拉各斯", "阿克拉",
        "马拉喀什", "卡萨布兰卡",
        "纽约", "洛杉矶", "芝加哥", "旧金山", "迈阿密", "华盛顿",
        "多伦多", "温哥华", "蒙特利尔",
        "墨西哥城", "哈瓦那", "波哥大", "利马", "圣地亚哥", "布宜诺斯艾利斯", "里约", "圣保罗",
        "悉尼", "墨尔本", "珀斯", "布里斯班", "奥克兰", "惠灵顿",
    ]
    titles.update(extra_cities)

    # 6. Add more keyword-based titles for broader coverage
    # Geography terms
    geo_terms = [
        "山", "河", "湖", "海", "岛", "半岛", "海峡", "海湾", "高原", "平原",
        "盆地", "丘陵", "峡谷", "瀑布", "温泉", "火山", "冰川", "沙漠", "绿洲",
        "森林", "草原", "湿地", "沼泽", "珊瑚礁", "红树林",
    ]
    # History/culture terms
    culture_terms = [
        "古希腊", "古罗马", "古埃及", "古巴比伦", "古印度", "古代中国",
        "文艺复兴", "工业革命", "法国大革命", "美国独立战争",
        "第一次世界大战", "第二次世界大战", "冷战",
        "丝绸之路", "大航海时代", "启蒙运动",
    ]
    # Science terms
    science_terms = [
        "地球", "太阳系", "银河系", "宇宙", "黑洞", "恒星", "行星",
        "大气层", "海洋", "板块构造", "地震", "火山喷发", "台风",
        "生物多样性", "生态系统", "进化论", "基因", "DNA",
    ]
    # Additional geographic features
    geo_features = [
        "长江", "黄河", "珠江", "松花江", "雅鲁藏布江",
        "亚马逊河", "尼罗河", "密西西比河", "多瑙河", "莱茵河",
        "贝加尔湖", "里海", "黑海", "红海", "地中海", "波斯湾",
        "渤海", "黄海", "东海", "南海",
        "喜马拉雅山脉", "阿尔卑斯山脉", "安第斯山脉", "落基山脉",
        "青藏高原", "云贵高原", "黄土高原", "内蒙古高原",
        "塔里木盆地", "准噶尔盆地", "四川盆地", "柴达木盆地",
    ]
    titles.update(geo_terms)
    titles.update(culture_terms)
    titles.update(science_terms)
    titles.update(geo_features)

    print(f"  Total titles to extract: {len(titles)}")
    print(f"  Extracting articles from ZIM...")

    # Extract articles
    articles = {}
    found = 0
    not_found = 0
    for title in sorted(titles):
        for candidate in [title, f"{title} (地理)", f"{title} (地质学)"]:
            try:
                art = zim.get_article_by_url("C", candidate)
                if art and art.data:
                    html = art.data.decode("utf-8", errors="replace")
                    # Extract first paragraph
                    import re
                    m = re.search(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
                    if m:
                        text = m.group(1)
                        text = re.sub(r"<sup[^>]*>.*?</sup>", "", text, flags=re.DOTALL)
                        text = re.sub(r"<[^>]+>", "", text)
                        text = re.sub(r"\s+", " ", text).strip()
                        if len(text) > 20:
                            articles[title] = text[:500]
                            found += 1
                            break
            except Exception:
                continue
        else:
            not_found += 1

    # Save
    out_path = OUT / "knowledge_zh_mini.json"
    out_path.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024
    print(f"  Saved: {out_path.name} ({size_kb:.0f} KB, {found} articles, {not_found} not found)")


def build_places_subset():
    """Extract major cities from places.db into a smaller file."""
    db_path = DATA / "places.db"
    if not db_path.exists():
        print("  ⚠️ places.db not found, skipping")
        return

    import sqlite3
    print("  Querying places.db for major cities...")
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Get cities with population > 100000
    try:
        cur.execute("""
            SELECT name, ascii, lat, lon, country, pop, fclass, fcode
            FROM places
            WHERE fclass = 'P'
            AND pop > 50000
            ORDER BY pop DESC
        """)
        rows = cur.fetchall()
    except Exception as e:
        print(f"  ⚠️ Query failed: {e}")
        conn.close()
        return

    conn.close()

    # Build compact format
    cities = []
    for row in rows:
        name, ascii, lat, lon, cc, pop, fclass, fcode = row
        cities.append({
            "name": name,
            "lat": lat,
            "lon": lon,
            "cc": cc,
            "pop": pop or 0,
        })

    out_path = OUT / "cities_major.json"
    out_path.write_text(json.dumps(cities, ensure_ascii=False), encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024
    print(f"  Saved: {out_path.name} ({size_kb:.0f} KB, {len(cities)} cities)")


def build_tile_index():
    """Build a summary of available terrain tiles."""
    tiles_dir = DATA / "tiles"
    if not tiles_dir.exists():
        print("  ⚠️ No tiles directory")
        return

    tiles = []
    for f in sorted(tiles_dir.glob("*.npz")):
        if f.name == "index.json":
            continue
        try:
            import numpy as np
            d = np.load(f)
            tiles.append({
                "file": f.name,
                "lat_min": float(d["lat_min"]),
                "lat_max": float(d["lat_max"]),
                "lon_min": float(d["lon_min"]),
                "lon_max": float(d["lon_max"]),
                "size_kb": f.stat().st_size // 1024,
            })
        except Exception:
            pass

    out_path = OUT / "tiles_index.json"
    out_path.write_text(json.dumps(tiles, indent=2), encoding="utf-8")
    print(f"  Saved: {out_path.name} ({len(tiles)} tiles)")


if __name__ == "__main__":
    print("Building offline data pack...\n")

    print("[1/3] ZIM subset:")
    build_zim_subset()

    print("\n[2/3] Places subset:")
    build_places_subset()

    print("\n[3/3] Tile index:")
    build_tile_index()

    # Total size
    total = sum(f.stat().st_size for f in OUT.iterdir() if f.is_file())
    print(f"\nTotal offline pack: {total / 1024 / 1024:.1f} MB")
    print(f"Location: {OUT}")
