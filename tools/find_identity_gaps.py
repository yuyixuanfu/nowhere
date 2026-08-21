#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Card 65: Identity Gap Scanner -- find places with insufficient identity layers.

Scans explorable_index for "identity score" = humanities_cards*2 + localcolor_cards*1
+ seasonal(1/0) + festivals(1/0) + flora(1/0).

Threshold calibrated: score < 9 = identity absent.
  Calibration places:
    鹤岗 (not in index) = 0  -> absent
    威海卫              = 8  -> absent
    京都                = 37 -> present
    成都                = 23 -> present

Output: nowhere/data/identity_gaps_report.md

Usage:
    cd C:\\Users\\84989\\Desktop\\nowhere_repo
    python tools/find_identity_gaps.py
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# GBK console fix
sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "nowhere" / "data"
REPORT_PATH = DATA_DIR / "identity_gaps_report.md"

# ── Threshold (calibrated against 鹤岗/威海卫/京都/成都) ────────────────
IDENTITY_THRESHOLD = 9  # score < 9 = identity absent

# Calibration places pinned to top of report
PINNED = {"威海卫", "京都", "成都"}


# ═══════════════════════════════════════════════════════════════════════
# Data loading
# ═══════════════════════════════════════════════════════════════════════

def load_json(name: str):
    path = DATA_DIR / name
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_seasonal_places() -> set[str]:
    """Extract place names from all seasonal_*.txt files."""
    places: set[str] = set()
    for sf in sorted(DATA_DIR.glob("seasonal*.txt")):
        with open(sf, "r", encoding="utf-8") as f:
            for line in f:
                for m in re.findall(r"\[([^\]|]+)\|[^\]]+\]", line):
                    m = m.strip()
                    if m and len(m) < 20:
                        places.add(m)
    # Also check scenes_src/seasonal.json
    try:
        seasonal_json = load_json("scenes_src/seasonal.json")
        if isinstance(seasonal_json, list):
            for item in seasonal_json:
                p = item.get("place", "")
                if p:
                    places.add(p)
    except Exception:
        pass
    return places


def load_localcolor() -> dict:
    """Merge all localcolor*.json files."""
    merged: dict = {}
    for fname in ["localcolor.json", "localcolor_china.json",
                   "localcolor_europe_middleeast.json",
                   "localcolor_japan_korea_sea.json",
                   "localcolor_americas_africa_oceania.json",
                   "localcolor_natural.json"]:
        d = load_json(fname)
        if isinstance(d, dict):
            merged.update(d)
    return merged


def load_festivals_places() -> set[str]:
    """Get set of place names that have festivals."""
    festivals = load_json("festivals.json")
    if not isinstance(festivals, list):
        return set()
    return {f["place"] for f in festivals if isinstance(f, dict) and f.get("place")}


# ═══════════════════════════════════════════════════════════════════════
# Population lookup via cities15000.txt (GeoNames)
# ═══════════════════════════════════════════════════════════════════════

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def load_geonames_population() -> dict[str, int]:
    """Load population from cities15000.txt, return list of (lat, lon, pop, name)."""
    cities_path = DATA_DIR / "packs" / "cities15000.txt"
    if not cities_path.exists():
        return []
    cities = []
    with open(cities_path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) < 19:
                continue
            try:
                lat = float(parts[4])
                lon = float(parts[5])
                pop = int(parts[14]) if parts[14] else 0
                name = parts[1]  # ascii name
                # Also get alternate names for Chinese matching
                alts = parts[3] if len(parts) > 3 else ""
                cities.append((lat, lon, pop, name, alts))
            except (ValueError, IndexError):
                continue
    return cities


def lookup_population(lat: float, lon: float, cities: list, max_km: float = 50.0) -> int:
    """Find population of nearest city within max_km."""
    best_pop = 0
    best_dist = max_km + 1
    for clat, clon, pop, name, alts in cities:
        # Quick bounding box filter
        if abs(clat - lat) > 1.0 or abs(clon - lon) > 1.0:
            continue
        dist = haversine_km(lat, lon, clat, clon)
        if dist < best_dist:
            best_dist = dist
            best_pop = pop
    return best_pop if best_dist <= max_km else 0


def build_chinese_pop_map(cities: list) -> dict[str, int]:
    """Build a mapping from Chinese city names to population using alternate names."""
    zh_map: dict[str, int] = {}
    for lat, lon, pop, name, alts in cities:
        if not alts:
            continue
        # Alternate names are comma-separated; look for Chinese characters
        for alt in alts.split(","):
            alt = alt.strip()
            if alt and re.match(r"^[一-鿿]+$", alt) and len(alt) >= 2:
                # Keep the highest population for each Chinese name
                if alt not in zh_map or pop > zh_map[alt]:
                    zh_map[alt] = pop
    return zh_map


# ═══════════════════════════════════════════════════════════════════════
# Identity score computation
# ═══════════════════════════════════════════════════════════════════════

class IdentityScorer:
    """Computes identity scores for places."""

    def __init__(self):
        self.humanities_places = load_json("humanities.json")
        if isinstance(self.humanities_places, dict):
            self.humanities_places = self.humanities_places.get("places", {})
        else:
            self.humanities_places = {}

        self.films = load_json("humanities_films.json") or {}
        self.hist = load_json("humanities_historical.json") or {}
        self.localcolor = load_localcolor()
        self.seasonal_places = load_seasonal_places()
        self.festivals_places = load_festivals_places()
        self.flora = load_json("flora_by_place.json") or {}

    def score(self, name: str) -> dict:
        """Return identity score breakdown for a place."""
        # Humanities cards
        h_cards = 0
        h_detail = {}
        if name in self.humanities_places:
            info = self.humanities_places[name]
            ev = len(info.get("事件", []))
            pe = len(info.get("人物", []))
            wo = len(info.get("作品", []))
            h_cards = ev + pe + wo
            h_detail = {"events": ev, "people": pe, "works": wo}
        if name in self.films:
            fc = len(self.films[name].get("作品", []))
            h_cards += fc
            h_detail["films"] = fc
        if name in self.hist:
            entry = self.hist[name]
            if isinstance(entry, dict):
                hc = len(entry.get("人物", []))
            elif isinstance(entry, list):
                hc = len(entry)
            else:
                hc = 0
            h_cards += hc
            h_detail["historical"] = hc

        # Localcolor cards
        lc_cards = 0
        lc_detail = {}
        if name in self.localcolor:
            info = self.localcolor[name]
            for k in ["物产", "声音", "痕迹", "美食"]:
                c = len(info.get(k, []))
                lc_cards += c
                if c:
                    lc_detail[k] = c
            rc = len(info.get("节律", []))
            lc_cards += rc
            if rc:
                lc_detail["节律"] = rc

        # Binary layers
        has_seasonal = 1 if name in self.seasonal_places else 0
        has_festivals = 1 if name in self.festivals_places else 0
        has_flora = 1 if name in self.flora else 0

        total = h_cards * 2 + lc_cards + has_seasonal + has_festivals + has_flora

        # Identify missing layers
        missing = []
        if h_cards == 0:
            missing.append("humanities")
        if lc_cards == 0:
            missing.append("localcolor")
        if not has_seasonal:
            missing.append("seasonal")
        if not has_festivals:
            missing.append("festivals")
        if not has_flora:
            missing.append("flora")

        return {
            "total": total,
            "humanities_cards": h_cards,
            "humanities_detail": h_detail,
            "localcolor_cards": lc_cards,
            "localcolor_detail": lc_detail,
            "seasonal": has_seasonal,
            "festivals": has_festivals,
            "flora": has_flora,
            "missing": missing,
            "is_absent": total < IDENTITY_THRESHOLD,
        }


# ═══════════════════════════════════════════════════════════════════════
# Health check hook (Card 29 integration)
# ═══════════════════════════════════════════════════════════════════════

def health_check() -> dict:
    """Run identity gap check for health.py integration.

    Returns dict with:
        pass: bool -- True if no identity gaps
        absent_count: int -- number of places below threshold
        total: int -- total places scanned
        message: str -- summary
    """
    idx = load_json("explorable_index.json")
    if not idx:
        return {"pass": False, "absent_count": -1, "total": 0,
                "message": "explorable_index.json not found"}

    all_places = idx.get("places", {})
    scorer = IdentityScorer()
    absent = []
    for name in all_places:
        result = scorer.score(name)
        if result["is_absent"]:
            absent.append(name)

    return {
        "pass": len(absent) == 0,
        "absent_count": len(absent),
        "total": len(all_places),
        "message": f"{len(absent)}/{len(all_places)} places below identity threshold ({IDENTITY_THRESHOLD})",
    }


# ═══════════════════════════════════════════════════════════════════════
# Report generation
# ═══════════════════════════════════════════════════════════════════════

def generate_report() -> tuple[str, dict]:
    """Generate identity_gaps_report.md. Returns (report_text, stats)."""
    idx = load_json("explorable_index.json")
    if not idx:
        return "# Identity Gaps Report\n\nexplorable_index.json not found.\n", {}

    all_places = idx.get("places", {})
    scorer = IdentityScorer()

    # Load population data
    print("  Loading GeoNames population data...")
    cities = load_geonames_population()
    zh_pop_map = build_chinese_pop_map(cities) if cities else {}
    print(f"  Loaded {len(cities)} cities, {len(zh_pop_map)} Chinese name mappings")

    # Score all places
    results: list[dict] = []
    for name, info in all_places.items():
        score_data = scorer.score(name)
        lat = info.get("lat")
        lon = info.get("lon")

        # Population lookup: try Chinese name match first, then coordinate proximity
        pop = 0
        if name in zh_pop_map:
            pop = zh_pop_map[name]
        elif lat is not None and lon is not None and cities:
            pop = lookup_population(lat, lon, cities)

        results.append({
            "name": name,
            "lat": lat,
            "lon": lon,
            "pop": pop,
            "is_pinned": name in PINNED,
            **score_data,
        })

    # Sort: pinned first, then by population descending, then by score ascending
    results.sort(key=lambda r: (
        not r["is_pinned"],
        -r["pop"],
        r["total"],
    ))

    # Stats
    absent_list = [r for r in results if r["is_absent"]]
    total = len(results)
    absent_count = len(absent_list)
    avg_score = sum(r["total"] for r in results) / total if total else 0

    # Group absent by missing layer pattern
    missing_patterns: dict[str, list] = defaultdict(list)
    for r in absent_list:
        key = "+".join(r["missing"]) if r["missing"] else "unknown"
        missing_patterns[key].append(r)

    stats = {
        "total": total,
        "absent_count": absent_count,
        "avg_score": round(avg_score, 1),
        "threshold": IDENTITY_THRESHOLD,
        "patterns": {k: len(v) for k, v in missing_patterns.items()},
    }

    # Build markdown
    lines: list[str] = []
    lines.append("# Identity Gaps Report")
    lines.append("")
    lines.append(f"**Generated**: scan of {total} places in explorable_index")
    lines.append(f"**Threshold**: identity score < {IDENTITY_THRESHOLD} = absent")
    lines.append(f"**Formula**: humanities_cards * 2 + localcolor_cards + seasonal(1/0) + festivals(1/0) + flora(1/0)")
    lines.append("")
    lines.append(f"**Absent**: {absent_count} / {total} places")
    lines.append(f"**Average score**: {avg_score:.1f}")
    lines.append("")

    # Calibration
    lines.append("## Calibration")
    lines.append("")
    lines.append("| Place | Score | Status |")
    lines.append("|-------|-------|--------|")
    for r in results:
        if r["is_pinned"]:
            status = "absent" if r["is_absent"] else "present"
            lines.append(f"| {r['name']} | {r['total']} | {status} |")
    lines.append("")

    # Missing layer patterns
    lines.append("## Missing Layer Patterns (Absent Places)")
    lines.append("")
    lines.append("| Pattern | Count | Description |")
    lines.append("|---------|-------|-------------|")
    for pattern, group in sorted(missing_patterns.items(), key=lambda x: -len(x[1])):
        desc = _pattern_description(pattern)
        lines.append(f"| {pattern} | {len(group)} | {desc} |")
    lines.append("")

    # Full absent list
    lines.append("## Absent Places (Full List)")
    lines.append("")
    lines.append("| # | Place | Pop | Score | H | LC | S | F | FL | Missing |")
    lines.append("|---|-------|-----|-------|---|----|----|---|-----|---------|")
    for i, r in enumerate(absent_list, 1):
        pop_str = f"{r['pop']:,}" if r["pop"] > 0 else "-"
        miss_str = ", ".join(r["missing"]) if r["missing"] else "-"
        lines.append(
            f"| {i} | {r['name']} | {pop_str} | {r['total']} | "
            f"{r['humanities_cards']} | {r['localcolor_cards']} | "
            f"{r['seasonal']} | {r['festivals']} | {r['flora']} | {miss_str} |"
        )
    lines.append("")

    # Full scan (all places, for reference)
    lines.append("## Full Scan (All Places)")
    lines.append("")
    lines.append("<details>")
    lines.append("<summary>Click to expand full 633-place scan</summary>")
    lines.append("")
    lines.append("| # | Place | Pop | Score | H | LC | S | F | FL | Status |")
    lines.append("|---|-------|-----|-------|---|----|----|---|-----|--------|")
    for i, r in enumerate(results, 1):
        pop_str = f"{r['pop']:,}" if r["pop"] > 0 else "-"
        status = "ABSENT" if r["is_absent"] else "ok"
        lines.append(
            f"| {i} | {r['name']} | {pop_str} | {r['total']} | "
            f"{r['humanities_cards']} | {r['localcolor_cards']} | "
            f"{r['seasonal']} | {r['festivals']} | {r['flora']} | {status} |"
        )
    lines.append("")
    lines.append("</details>")
    lines.append("")

    # Card 54 batch plan
    lines.append("## Batch Plan (for Card 54)")
    lines.append("")
    lines.append("Priority order: fill \"all-missing\" places first, then layer-specific gaps.")
    lines.append("")

    # Group by missing count: all-missing = 5/5 layers gone, severe = 3-4, partial = 1-2
    all_missing = [r for r in absent_list if len(r["missing"]) >= 5]
    severe = [r for r in absent_list if 3 <= len(r["missing"]) < 5]
    partial = [r for r in absent_list if 1 <= len(r["missing"]) < 3]

    if all_missing:
        lines.append(f"### Batch 1: Completely empty ({len(all_missing)} places, batch size <= 30)")
        lines.append("")
        lines.append("Each place needs full ecosystem: localcolor 5 + humanities 2 + seasonal 2 + festivals 1 + souvenirs 2 + flora 5-8")
        lines.append("")
        for i, r in enumerate(all_missing[:30], 1):
            pop_str = f"{r['pop']:,}" if r["pop"] > 0 else "-"
            lines.append(f"{i}. **{r['name']}** (pop {pop_str}, score {r['total']})")
        if len(all_missing) > 30:
            lines.append(f"- ... and {len(all_missing) - 30} more")
        lines.append("")

    if severe:
        lines.append(f"### Batch 2: Severe gaps ({len(severe)} places, 3-4 layers missing)")
        lines.append("")
        for i, r in enumerate(severe[:30], 1):
            miss_str = ", ".join(r["missing"])
            pop_str = f"{r['pop']:,}" if r["pop"] > 0 else "-"
            lines.append(f"{i}. **{r['name']}** (pop {pop_str}, score {r['total']}, missing: {miss_str})")
        if len(severe) > 30:
            lines.append(f"- ... and {len(severe) - 30} more")
        lines.append("")

    if partial:
        lines.append(f"### Batch 3+: Partial gaps ({len(partial)} places, 1-2 layers missing)")
        lines.append("")
        for i, r in enumerate(partial[:30], 1):
            miss_str = ", ".join(r["missing"])
            lines.append(f"{i}. **{r['name']}** (score {r['total']}, missing: {miss_str})")
        if len(partial) > 30:
            lines.append(f"- ... and {len(partial) - 30} more")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Verification")
    lines.append("")
    lines.append("After batch fill, re-run:")
    lines.append("```")
    lines.append("python tools/find_identity_gaps.py")
    lines.append("```")
    lines.append("Expected: absent_count = 0 (or each remaining has \"宁缺\" annotation)")
    lines.append("")

    report = "\n".join(lines)
    return report, stats


def _pattern_description(pattern: str) -> str:
    """Human-readable description of missing layer pattern."""
    parts = pattern.split("+")
    total_layers = 5  # humanities, localcolor, seasonal, festivals, flora
    missing_count = len(parts)
    if missing_count >= total_layers:
        return "completely empty (all 5 identity layers missing)"
    desc_map = {
        "humanities": "no events/people/works",
        "localcolor": "no localcolor (物产/声音/痕迹/节律/美食)",
        "seasonal": "no seasonal descriptions",
        "festivals": "no festivals",
        "flora": "no plant data",
    }
    return " + ".join(desc_map.get(p, p) for p in parts)


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("Identity Gap Scanner -- Card 65")
    print("=" * 60)

    report, stats = generate_report()

    # Write report
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n  Report: {REPORT_PATH}")

    # Console summary
    print(f"\n  Total places: {stats.get('total', 0)}")
    print(f"  Absent (score < {IDENTITY_THRESHOLD}): {stats.get('absent_count', 0)}")
    print(f"  Average score: {stats.get('avg_score', 0)}")
    print(f"\n  Missing layer patterns:")
    for pattern, count in sorted(stats.get("patterns", {}).items(),
                                  key=lambda x: -x[1]):
        print(f"    {pattern}: {count} places")

    # Health check result
    print(f"\n  Health check: {'PASS' if stats.get('absent_count', -1) == 0 else 'FAIL'}")
    print("=" * 60)

    return 0 if stats.get("absent_count", -1) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
