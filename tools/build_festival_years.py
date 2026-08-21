"""Build complete lunar/hijri year tables (2026-2035) for festivals.json.

Run: python tools/build_festival_years.py

Requires: zhdate, hijri-converter (or hijridate)
"""

from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter
from datetime import date as _date, datetime as _datetime

# ── Lunar (zhdate) ──────────────────────────────────────────────────

try:
    from zhdate import ZhDate
except ImportError:
    print("ERROR: zhdate not installed. pip install zhdate", file=sys.stderr)
    sys.exit(1)

# ── Hijri ───────────────────────────────────────────────────────────

try:
    from hijridate import Hijri as _Hijri

    def _hijri_to_gregorian(h_year: int, h_month: int, h_day: int) -> tuple[int, int, int]:
        """Convert hijri date to (year, month, day) in Gregorian."""
        g = _Hijri(h_year, h_month, h_day).to_gregorian()
        return (g.year, g.month, g.day)
except ImportError:
    try:
        from hijri_converter import Hijri as _HijriConv

        def _hijri_to_gregorian(h_year: int, h_month: int, h_day: int) -> tuple[int, int, int]:
            g = _HijriConv(h_year, h_month, h_day).to_gregorian()
            return (g.year, g.month, g.day)
    except ImportError:
        print("ERROR: neither hijridate nor hijri-converter installed.", file=sys.stderr)
        sys.exit(1)

# Gregorian years to cover
_YEARS = list(range(2026, 2036))

# ── Known hijri festival definitions ────────────────────────────────
_HIJRI_NAME_MAP: dict[str, tuple[int, int]] = {
    "开斋节": (10, 1),
    "宰牲节": (12, 10),
    "阿舒拉": (1, 10),
    "伊斯坦布尔开斋节": (10, 1),
    "达卡开斋节": (10, 1),
    "卡拉奇开斋节": (10, 1),
    "撒马尔罕开斋节": (10, 1),
    "马拉喀什开斋节": (10, 1),
    "卡萨布兰卡开斋节": (10, 1),
    "开罗斋月夜市": (9, 1),   # Ramadan start
}


def _build_lunar_years(lunar_month: int, lunar_day: int) -> dict[str, list[int]]:
    """Build {year: [month, day]} for a lunar festival, 2026-2035."""
    years: dict[str, list[int]] = {}
    for yr in _YEARS:
        try:
            z = ZhDate(yr, lunar_month, lunar_day)
            g = z.to_datetime().date()
            years[str(yr)] = [g.month, g.day]
        except Exception:
            pass
    return years


def _build_hijri_years(hijri_month: int, hijri_day: int) -> dict[str, list[int]]:
    """Build {year: [month, day]} for a hijri festival, 2026-2035."""
    years: dict[str, list[int]] = {}
    for h_yr in range(1447, 1460):
        try:
            g_year, m, d = _hijri_to_gregorian(h_yr, hijri_month, hijri_day)
            if 2026 <= g_year <= 2035:
                key = str(g_year)
                if key not in years:
                    years[key] = [m, d]
        except Exception:
            pass
    return years


def _consensus_lunar_date(years: dict) -> tuple[int, int] | None:
    """Find the most common (lunar_month, lunar_day) from existing years.

    Uses majority vote, deterministic tie-breaking (earliest year wins).
    Returns None if no consistent date found.
    """
    votes: Counter[tuple[int, int]] = Counter()
    # Track first occurrence year for deterministic tie-breaking
    first_year: dict[tuple[int, int], int] = {}
    for yr_str, md in sorted(years.items()):
        if not md or len(md) < 2:
            continue
        try:
            g = _datetime(int(yr_str), md[0], md[1])
            z = ZhDate.from_datetime(g)
            pair = (z.lunar_month, z.lunar_day)
            votes[pair] += 1
            if pair not in first_year:
                first_year[pair] = int(yr_str)
        except Exception:
            continue

    if not votes:
        return None

    # Return the most common lunar date, break ties by earliest year
    best = max(votes.keys(), key=lambda p: (votes[p], -first_year.get(p, 9999)))
    return best


def _festivals_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "nowhere" / "data" / "festivals.json"


def main() -> None:
    fp = _festivals_path()
    data = json.loads(fp.read_text(encoding="utf-8"))

    updated = 0
    added_new = 0

    for fest in data:
        window = fest.get("window", {})
        wtype = window.get("type", "")
        name = fest.get("name", "")
        old_years = window.get("years", {})

        if wtype == "lunar":
            # Auto-detect lunar month/day from existing years
            pair = _consensus_lunar_date(old_years)
            if pair:
                new_years = _build_lunar_years(pair[0], pair[1])
                merged = {**old_years, **new_years}
                if merged != old_years:
                    window["years"] = merged
                    updated += 1
                    print(f"  [lunar] {name}: {len(merged)} years (lunar {pair[0]}/{pair[1]})")

        elif wtype == "hijri":
            # Use known mapping or try reverse lookup
            pair = _HIJRI_NAME_MAP.get(name)
            if not pair:
                # Try to find from the name containing known keywords
                for keyword, hm in _HIJRI_NAME_MAP.items():
                    if keyword in name or name in keyword:
                        pair = hm
                        break
            if pair:
                new_years = _build_hijri_years(pair[0], pair[1])
                merged = {**old_years, **new_years}
                if merged != old_years:
                    window["years"] = merged
                    updated += 1
                    print(f"  [hijri] {name}: {len(merged)} years (hijri {pair[0]}/{pair[1]})")

    # ── Add CN entry for 开斋节 if missing ─────────────────────────
    has_cn_eid = any(
        f.get("name") == "开斋节" and f.get("country") == "CN"
        for f in data
    )
    if not has_cn_eid:
        eid_years = _build_hijri_years(10, 1)
        data.append({
            "name": "开斋节",
            "place": "喀什",
            "country": "CN",
            "window": {
                "type": "hijri",
                "years": eid_years,
                "span_days": 3,
            },
            "cards": [
                "清真寺门口的鞋排了一地。里面在祈祷,诵经声从门缝里出来。你站在外面,听了一会儿。",
                "街上的人互相握手,小孩兜里塞满了糖。你接了一块,甜得粘牙。",
            ],
            "eve_cards": [
                "开斋节前一天,家家在炸油香。面团在油锅里膨胀,香味从巷口飘出来。",
            ],
        })
        added_new += 1
        print(f"  [new] 开斋节 CN: {len(eid_years)} years")

    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDone. Updated {updated} entries, added {added_new} new.")
    print(f"Total festivals: {len(data)}")


if __name__ == "__main__":
    main()
