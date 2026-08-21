"""加城市流水线——一条命令把新城市送进乌有乡。

用法:
    python tools/add_place.py 南京          # 跑前三关(坐标/实据/模板)
    python tools/add_place.py --check 南京  # 质检关
    python tools/add_place.py --merge 南京  # 合并关

GBK 提示: 所有 print 前已 reconfigure utf-8。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

_REPO = pathlib.Path(__file__).resolve().parent.parent
_DATA_DIR = _REPO / "nowhere" / "data"
_DRAFTS_DIR = _REPO / "drafts"
_LC_PATH = _DATA_DIR / "localcolor.json"
_IDX_PATH = _DATA_DIR / "explorable_index.json"

# ── 禁词表(来自 WRITING_PROMPT.md) ──────────────────────────────────

_FORBIDDEN_WORDS = [
    "很", "非常", "十分", "巨大", "美丽",
    "一些", "很多", "感觉", "仿佛", "好像", "似乎", "有点",
]

# 时间语境假阳性过滤: 这些短语里的禁词不算违规
# 例如 "凌晨三点" 里的 "三" 不是禁词 "十分" 的一部分;
# "很多年前" 里的 "很多" 仍算违规,但 "感觉很冷" 中 "很" 算违规。
# 我们用"如果禁词出现在特定时间短语里就跳过"的策略。
_TIME_CONTEXT_PATTERNS = [
    # "三点/四点/.../十二点" 里的 "三/四" 不是禁词
    re.compile(r"[一二三四五六七八九十两]+点"),
    # "十分" 出现在 "几点十分" 里(时间分钟)不是禁词
    re.compile(r"\d+时\d*分"),
    re.compile(r"[一二三四五六七八九十两]+时[一二三四五六七八九十两零\d]*分"),
    # "几百年/千年/万年" 等时间量词不是 "很多"
    re.compile(r"[\d一二三四五六七八九十两百千万零]+年"),
    # "很多年前" 仍算违规——不放行
    # "十分" 在 "十分罕见/十分壮观" 里是禁词,但在 "两点十分" 里不是
    re.compile(r"[一二三四五六七八九十两\d]+点[一二三四五六七八九十两\d]*分"),
    re.compile(r"\d+:\d+"),  # 14:30 这种格式
]


def _is_time_context(text: str, word: str, pos: int) -> bool:
    """判断禁词出现位置是否在时间语境中(假阳性过滤)。

    pos: word 在 text 中的起始下标(re.finditer 的 m.start())。
    """
    # 取禁词前后各 6 字符的窗口做检查(中文时间短语可能较长)
    window_start = max(0, pos - 6)
    window_end = min(len(text), pos + len(word) + 6)
    window = text[window_start:window_end]
    for pat in _TIME_CONTEXT_PATTERNS:
        if pat.search(window):
            return True
    return False


# ── Gate 1: 坐标关 ──────────────────────────────────────────────────

def gate_coordinate(place: str, *, _geocode_lookup=None, _country_code_of=None,
                    _is_water=None) -> dict:
    """查坐标 + 反查国家 + 水面检测。

    返回 {"ok": bool, "lat": float, "lon": float, "country": str,
           "is_water": bool, "error": str|None}
    """
    from nowhere import geocode as _geocode_mod
    from nowhere import country as _country_mod
    from nowhere import terrain as _terrain_mod

    lookup_fn = _geocode_lookup or (lambda p: asyncio.get_event_loop().run_until_complete(_geocode_mod.lookup(p)))
    country_fn = _country_code_of or _country_mod.country_code_of
    water_fn = _is_water or _terrain_mod.is_water

    result = {"ok": False, "lat": None, "lon": None, "country": None,
              "is_water": False, "error": None}

    coords = lookup_fn(place)
    if coords is None:
        result["error"] = f"\033[31m[坐标关] 地名 '{place}' 查不到坐标。\033[0m"
        return result

    lat, lon = coords
    result["lat"] = lat
    result["lon"] = lon

    cc = country_fn(lat, lon)
    result["country"] = cc

    water = water_fn(lat, lon)
    result["is_water"] = water

    if water:
        result["error"] = (
            f"\033[31m[坐标关] {place} 落在水面上! "
            f"坐标 ({lat:.4f}, {lon:.4f}) → surface=water。\n"
            f"  建议: 检查地名是否正确,或手动指定附近陆地坐标。\033[0m"
        )
        return result

    # 如果能查到国家码,做基本合理性检查(不硬拦,但警告)
    if cc is not None:
        print(f"  [坐标关] {place} → ({lat:.4f}, {lon:.4f}), 国家={cc}")
    else:
        print(f"  [坐标关] {place} → ({lat:.4f}, {lon:.4f}), 国家码不可用(数据包缺失)")

    result["ok"] = True
    return result


# ── Gate 2: 实据关 ──────────────────────────────────────────────────

def gate_facts(place: str, *, _zim_lookup=None) -> pathlib.Path | None:
    """从 ZIM 拉 Wikipedia 条目,存到 drafts/{place}_facts.md。

    返回保存路径,失败返回 None。
    """
    out_path = _DRAFTS_DIR / f"{place}_facts.md"

    # 幂等: 已有草稿不覆盖(可能是人改过的)
    if out_path.exists():
        print(f"  [实据关] {out_path.name} 已存在,跳过(不覆盖人工编辑)")
        return out_path

    # 尝试用 ZIM 查
    extract = None
    if _zim_lookup is not None:
        extract = _zim_lookup(place)
    else:
        extract = _zim_read(place)

    if extract:
        _DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            f"# {place} — Wikipedia 实据\n\n"
            f"来源: wikipedia_zh_mini.zim (离线)\n\n"
            f"## 摘要\n\n{extract}\n\n"
            f"---\n\n"
            f"以上内容供写卡参考,请基于此撰写五类方志卡。\n",
            encoding="utf-8",
        )
        print(f"  [实据关] 已保存 {out_path.name} ({len(extract)} 字)")
        return out_path
    else:
        print(f"  [实据关] ZIM 中未找到 '{place}' 条目,跳过(可手动补充)")
        return None


def _zim_read(place: str) -> str | None:
    """尝试从 ZIM 读取条目摘要(简化版,只做直接查找)。"""
    zim_path = _DATA_DIR / "packs" / "wikipedia_zh_mini.zim"
    if not zim_path.exists():
        return None
    try:
        from zimply.zimply import ZIMFile
        zim = ZIMFile(str(zim_path), encoding="utf-8")
        art = zim.get_article_by_url("C", place)
        if art is not None and art.data is not None:
            # 简单提取纯文本
            text = art.data
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
            paragraphs = []
            for m in re.finditer(r"<p[^>]*>(.*?)</p>", text, re.DOTALL):
                p = m.group(1)
                p = re.sub(r"<sup[^>]*>.*?</sup>", "", p, flags=re.DOTALL)
                p = re.sub(r"<[^>]+>", "", p)
                p = re.sub(r"\s+", " ", p).strip()
                if p:
                    paragraphs.append(p)
            if paragraphs:
                return "\n\n".join(paragraphs[:10])  # 前 10 段
    except Exception:
        pass
    return None


# ── Gate 3: 模板关 ──────────────────────────────────────────────────

def gate_template(place: str) -> pathlib.Path:
    """生成 drafts/{place}_cards.json 骨架。

    每个字段带注释说明(以 _注释_ 前缀的键)。
    """
    _DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _DRAFTS_DIR / f"{place}_cards.json"

    if out_path.exists():
        print(f"  [模板关] {out_path.name} 已存在,跳过(不覆盖人工编辑)")
        return out_path

    skeleton = {
        "_说明": {
            "用法": "填写五类卡后,用 python tools/add_place.py --check {place} 质检",
            "卡格式": "每条卡 = 1-3 句散文,不要百科腔/攻略腔",
            "禁词": "不许用: 很/非常/十分/巨大/美丽/一些/很多/感觉/仿佛/好像/似乎/有点",
            "hours": "[起,止) 左闭右开,[6,8]=6:00-7:59有效;跨午夜用[22,24]不用[22,1]",
            "months": "不写=全年有效;季节限定写月份列表如[6,7,8]",
        },
        "物产": [
            "【请填写】当地能摸到、看到、吃到的实物。1-3句散文。"
        ],
        "声音": [
            "【请填写】当地特有的声音。1-3句散文。"
        ],
        "痕迹": [
            "【请填写】时间留下的印记——旧的、坏的、被改过的。1-3句散文。"
        ],
        "植被": [
            "【请填写】当地植物。1-3句散文。"
        ],
        "美食": [
            "【请填写】当地食物(权重3.0,饭点翻倍)。1-3句散文。"
        ],
        "节律": [
            {
                "_注释_hours": "[起,止) 左闭右开,0-24,跨午夜用[22,24]",
                "hours": [6, 8],
                "_注释_months": "不写此字段=全年有效;季节限定写[6,7,8]",
                "text": "【请填写】此时段的地方面貌。"
            }
        ],
    }

    out_path.write_text(
        json.dumps(skeleton, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  [模板关] 已生成 {out_path.name}")
    return out_path


# ── Gate 4: 质检关 ──────────────────────────────────────────────────

def gate_check(place: str, *, _geocode_lookup=None, _country_code_of=None,
               _is_water=None) -> list[str]:
    """质检: 禁词/节律/键名/坐标。返回问题列表(空=全过)。"""
    errors: list[str] = []
    cards_path = _DRAFTS_DIR / f"{place}_cards.json"

    if not cards_path.exists():
        errors.append(f"[质检关] {cards_path.name} 不存在,请先跑流水线生成模板")
        return errors

    cards = json.loads(cards_path.read_text(encoding="utf-8"))

    # 4a: 键名一致性——顶层键(去掉 _开头 的注释键)应与文件名匹配
    real_keys = [k for k in cards.keys() if not k.startswith("_")]
    expected_cats = {"物产", "声音", "痕迹", "植被", "美食"}
    missing = expected_cats - set(real_keys)
    if missing:
        errors.append(f"[质检关] 缺少类目: {missing}")

    # 4b: 禁词扫描
    for cat in real_keys:
        items = cards.get(cat, [])
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if isinstance(item, str):
                texts_to_check = [item]
            elif isinstance(item, dict):
                texts_to_check = [item.get("text", "")]
            else:
                continue
            for text in texts_to_check:
                for word in _FORBIDDEN_WORDS:
                    for m in re.finditer(re.escape(word), text):
                        pos = m.start()
                        if not _is_time_context(text, word, pos):
                            errors.append(
                                f"[质检关] 禁词「{word}」在 {cat}[{idx}]: "
                                f"...{text[max(0,pos-5):pos+len(word)+5]}..."
                            )

    # 4c: 节律 hours 合法性
    rhythm = cards.get("节律", [])
    if isinstance(rhythm, list):
        for idx, r in enumerate(rhythm):
            if isinstance(r, dict):
                hours = r.get("hours")
                if hours is not None:
                    if not (isinstance(hours, list) and len(hours) == 2):
                        errors.append(f"[质检关] 节律[{idx}] hours 格式错误: {hours}")
                    else:
                        start, end = hours
                        if not (0 <= start <= 24 and 0 <= end <= 24):
                            errors.append(f"[质检关] 节律[{idx}] hours 越界: {hours}")
                        if start >= end:
                            errors.append(f"[质检关] 节律[{idx}] hours 起>=止(跨午夜应拆): {hours}")

    # 4d: 坐标仍有效
    coord_result = gate_coordinate(place, _geocode_lookup=_geocode_lookup,
                                   _country_code_of=_country_code_of,
                                   _is_water=_is_water)
    if not coord_result["ok"]:
        errors.append(f"[质检关] 坐标校验失败: {coord_result['error']}")

    return errors


# ── Gate 5: 合并关 ──────────────────────────────────────────────────

def gate_merge(place: str, *, _geocode_lookup=None, _country_code_of=None,
               _is_water=None, _skip_tests: bool = False) -> list[str]:
    """合并: localcolor.json + explorable_index + 跑测试。返回问题列表。"""
    errors: list[str] = []

    # 先跑质检
    qc_errors = gate_check(place, _geocode_lookup=_geocode_lookup,
                           _country_code_of=_country_code_of,
                           _is_water=_is_water)
    if qc_errors:
        return qc_errors

    cards_path = _DRAFTS_DIR / f"{place}_cards.json"
    cards = json.loads(cards_path.read_text(encoding="utf-8"))

    # 5a: 键冲突检查
    lc_data = json.loads(_LC_PATH.read_text(encoding="utf-8")) if _LC_PATH.exists() else {}
    if place in lc_data:
        errors.append(f"\033[31m[合并关] '{place}' 已存在于 localcolor.json,键冲突!\033[0m")
        return errors

    # 5b: 写入 localcolor.json
    entry = {}
    for cat in ("物产", "声音", "痕迹", "植被", "美食"):
        items = cards.get(cat, [])
        # 过滤掉占位符
        real_items = [t for t in items if isinstance(t, str) and not t.startswith("【请填写】")]
        if real_items:
            entry[cat] = real_items

    rhythm = cards.get("节律", [])
    if rhythm:
        # 过滤掉占位符
        real_rhythm = []
        for r in rhythm:
            if isinstance(r, str) and not r.startswith("【请填写】"):
                real_rhythm.append(r)
            elif isinstance(r, dict):
                text = r.get("text", "")
                if not text.startswith("【请填写】"):
                    # 去掉注释键
                    clean = {k: v for k, v in r.items() if not k.startswith("_")}
                    real_rhythm.append(clean)
        if real_rhythm:
            entry["节律"] = real_rhythm

    if not entry:
        errors.append("[合并关] 卡片全为空(全是占位符),拒绝合并")
        return errors

    lc_data[place] = entry
    _LC_PATH.write_text(
        json.dumps(lc_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  [合并关] 已写入 localcolor.json: {place}")

    # 5c: 注册 explorable_index
    coord_result = gate_coordinate(place, _geocode_lookup=_geocode_lookup,
                                   _country_code_of=_country_code_of,
                                   _is_water=_is_water)
    idx_data = json.loads(_IDX_PATH.read_text(encoding="utf-8")) if _IDX_PATH.exists() else {"places": {}}
    if "places" not in idx_data:
        idx_data["places"] = {}
    if place not in idx_data["places"]:
        idx_data["places"][place] = {
            "lat": coord_result["lat"],
            "lon": coord_result["lon"],
            "layers": {
                "localcolor": True,
            },
        }
        _IDX_PATH.write_text(
            json.dumps(idx_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  [合并关] 已注册 explorable_index: {place}")
    else:
        print(f"  [合并关] {place} 已在 explorable_index 中,跳过注册")

    # 5d: 跑测试
    if not _skip_tests:
        print("  [合并关] 运行测试...")
        for test_mod in ("nowhere.tests.test_localcolor", "nowhere.tests.test_humanities"):
            ret = subprocess.run(
                [sys.executable, "-m", "pytest", test_mod, "-q", "--tb=short"],
                capture_output=True, text=True, encoding="utf-8",
            )
            if ret.returncode != 0:
                errors.append(f"[合并关] 测试 {test_mod} 失败:\n{ret.stdout}\n{ret.stderr}")
            else:
                print(f"    {test_mod}: PASS")

    return errors


# ── 幂等检查 ────────────────────────────────────────────────────────

def check_already_exists(place: str) -> bool:
    """检查地方是否已存在于 localcolor.json 或 explorable_index。"""
    lc_data = json.loads(_LC_PATH.read_text(encoding="utf-8")) if _LC_PATH.exists() else {}
    return place in lc_data


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="加城市流水线——一条命令把新城市送进乌有乡",
    )
    parser.add_argument("place", help="城市名(如: 南京)")
    parser.add_argument("--check", action="store_true",
                        help="质检关: 检查草稿卡片质量")
    parser.add_argument("--merge", action="store_true",
                        help="合并关: 写入数据文件 + 注册 + 跑测试")
    args = parser.parse_args()

    place = args.place.strip()
    if not place:
        print("\033[31m错误: 地名不能为空\033[0m")
        sys.exit(1)

    print(f"{'='*50}")
    print(f"加城市流水线: {place}")
    print(f"{'='*50}")

    # 幂等检查
    if check_already_exists(place):
        print(f"\n\033[33m[提示] '{place}' 已存在于 localcolor.json。\033[0m")
        if args.merge:
            print("\033[31m[拒绝] 不允许覆盖已有地名。如需更新,请手动编辑。\033[0m")
            sys.exit(1)
        elif not args.check:
            print("  如需质检,运行: python tools/add_place.py --check " + place)
            sys.exit(0)

    if args.check:
        # 只跑质检
        print(f"\n--- 质检关 ---")
        errors = gate_check(place)
        if errors:
            print(f"\n\033[31m质检未通过 ({len(errors)} 个问题):\033[0m")
            for e in errors:
                print(f"  \033[31m✗ {e}\033[0m")
            sys.exit(1)
        else:
            print(f"\n\033[32m质检全过! 可以合并: python tools/add_place.py --merge {place}\033[0m")
            sys.exit(0)

    if args.merge:
        # 合并关(内部会先跑质检)
        print(f"\n--- 合并关 ---")
        errors = gate_merge(place)
        if errors:
            print(f"\n\033[31m合并失败 ({len(errors)} 个问题):\033[0m")
            for e in errors:
                print(f"  \033[31m✗ {e}\033[0m")
            sys.exit(1)
        else:
            print(f"\n\033[32m合并完成! '{place}' 已加入乌有乡。\033[0m")
            sys.exit(0)

    # 默认: 跑前三关
    # Gate 1
    print(f"\n--- 坐标关 ---")
    coord = gate_coordinate(place)
    if not coord["ok"]:
        print(coord["error"])
        sys.exit(1)

    # Gate 2
    print(f"\n--- 实据关 ---")
    facts_path = gate_facts(place)

    # Gate 3
    print(f"\n--- 模板关 ---")
    template_path = gate_template(place)

    # 汇总
    print(f"\n{'='*50}")
    print(f"前三关完成!")
    print(f"  坐标: ({coord['lat']:.4f}, {coord['lon']:.4f}), 国家={coord['country']}")
    if facts_path:
        print(f"  实据: {facts_path}")
    print(f"  模板: {template_path}")
    print(f"\n下一步:")
    print(f"  1. 编辑 {template_path},填写五类卡 + 节律")
    print(f"  2. python tools/add_place.py --check {place}")
    print(f"  3. python tools/add_place.py --merge {place}")


if __name__ == "__main__":
    main()
