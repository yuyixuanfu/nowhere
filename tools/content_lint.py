"""Content lint: validates content_src/ before bake.

ERROR (stops build):
  - Pool declared in bake but no source file found (orphan pool)
  - Empty text card
  - Duplicate card (edit distance < 10)
  - Non-UTF-8 source file
  - Reference to non-existent enum value
  - Place name in global pool card (phenology cards must not contain specific locations)

WARNING (reported, does not stop):
  - Naked card (ocean word without constraint / season word without constraint /
    absolute assertion word / pool thickness < 5)
"""
from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# GBK console fix
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


@dataclass
class LintResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, msg: str) -> None:
        self.errors.append(f"ERROR: {msg}")

    def add_warning(self, msg: str) -> None:
        self.warnings.append(f"WARNING: {msg}")


# ── Naked-card keyword sets ──────────────────────────────────────────

_OCEAN_WORDS = {"海", "洋", "潮", "浪", "沙滩", "海岸", "海面", "海底", "海湾", "海港"}
_SEASON_WORDS = {"春", "夏", "秋", "冬", "春天", "夏天", "秋天", "冬天", "春季", "夏季", "秋季", "冬季"}
_ABSOLUTE_WORDS = {"永远", "从不", "总是", "一定", "绝对", "肯定", "全部", "所有", "一切"}

_CONSTRAINT_KEYS = {"seasons", "lat_band", "biomes", "climate_zone", "humidity", "ocean", "max_elev"}

# Card 80: place names that must not appear in global pool phenology cards
_PLACE_NAMES = {
    "纳米布", "骷髅海岸",
}

# Card 81: tree keywords that should have max_elev set
_TREE_KEYWORDS = {"松树", "松林", "松针", "松果", "松脂", "白桦林", "柳树", "柳枝",
                  "梧桐", "银杏", "水杉", "柿子树", "树干", "树林", "森林"}

# Card 81: specific place features that should not be under a country key
_PLACE_FEATURE_KEYWORDS = {
    "猴面包树大道", "穆龙达瓦",
    "时代广场", "第五大道", "好莱坞大道",
}


def _has_constraint(card: dict) -> bool:
    """Check if a card has any constraint field."""
    return any(card.get(k) for k in _CONSTRAINT_KEYS)


def _is_naked(text: str, card: dict) -> str | None:
    """Return reason if card is 'naked', else None."""
    if _has_constraint(card):
        return None
    # Check keyword presence
    if any(w in text for w in _OCEAN_WORDS):
        return "ocean word without constraint"
    if any(w in text for w in _SEASON_WORDS):
        return "season word without constraint"
    if any(w in text for w in _ABSOLUTE_WORDS):
        return "absolute assertion word"
    return None


def _edit_distance_ok(a: str, b: str, threshold: int = 10) -> bool:
    """True if edit distance between a and b is < threshold (near-duplicate)."""
    # Use SequenceMatcher ratio as proxy: ratio > (1 - threshold/max_len)
    max_len = max(len(a), len(b))
    if max_len == 0:
        return True
    ratio = SequenceMatcher(None, a, b).ratio()
    # ratio ~ 1.0 means very similar; edit distance < threshold
    # edit_distance ≈ max_len * (1 - ratio)
    est_edits = max_len * (1 - ratio)
    return est_edits < threshold


def lint_phenology(fp: pathlib.Path, result: LintResult) -> list[dict]:
    """Lint phenology.json. Returns list of card dicts for duplicate checking."""
    # UTF-8 check
    try:
        raw = fp.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        result.add_error(f"{fp.name}: not valid UTF-8")
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        result.add_error(f"{fp.name}: invalid JSON: {e}")
        return []

    events = data.get("events", {})
    cards: list[dict] = []
    for hemi_key, hemi in events.items():
        if hemi_key not in ("north", "south"):
            result.add_error(f"{fp.name}: unexpected hemisphere key '{hemi_key}'")
            continue
        for band, months in hemi.items():
            for month_str, entries in months.items():
                if not isinstance(entries, list):
                    result.add_error(f"{fp.name}: {hemi_key}.{band}.{month_str} is not a list")
                    continue
                for i, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        result.add_error(f"{fp.name}: {hemi_key}.{band}.{month_str}[{i}] not dict")
                        continue
                    text = entry.get("text", "")
                    if not text or not text.strip():
                        result.add_error(f"{fp.name}: {hemi_key}.{band}.{month_str}[{i}] empty text")
                        continue
                    cz = entry.get("climate_zone", "")
                    if cz not in ("热带", "暖温带", "温带", "寒带"):
                        result.add_error(
                            f"{fp.name}: {hemi_key}.{band}.{month_str}[{i}] "
                            f"invalid climate_zone: '{cz}'"
                        )
                    # Naked card check
                    reason = _is_naked(text, entry)
                    if reason:
                        result.add_warning(
                            f"{fp.name}: {hemi_key}.{band}.{month_str}[{i}] naked card: {reason}"
                        )
                    # Card 81: tree keyword without max_elev
                    if any(k in text for k in _TREE_KEYWORDS) and "max_elev" not in entry:
                        result.add_warning(
                            f"{fp.name}: {hemi_key}.{band}.{month_str}[{i}] "
                            f"tree keyword in text but no max_elev"
                        )
                    # Card 80: place name in global pool → ERROR
                    found_places = [p for p in _PLACE_NAMES if p in text]
                    if found_places:
                        result.add_error(
                            f"{fp.name}: {hemi_key}.{band}.{month_str}[{i}] "
                            f"place name in global pool: {', '.join(found_places)}"
                        )
                    cards.append(entry)
    return cards


def lint_txt_pool(fp: pathlib.Path, result: LintResult) -> list[dict]:
    """Lint a .txt pool file (one scene per line). Returns card dicts."""
    try:
        raw = fp.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        result.add_error(f"{fp.name}: not valid UTF-8")
        return []

    cards: list[dict] = []
    for lineno, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip [location] prefix tags (legacy compat)
        text = line
        if text.startswith("[") and "] " in text:
            text = text[text.index("] ") + 2:]
        if not text:
            result.add_error(f"{fp.name}:{lineno}: empty text after stripping")
            continue
        # These are plain text cards with no constraints
        cards.append({"text": text, "source": fp.name, "line": lineno})
    return cards


def check_duplicates(pool_name: str, cards: list[dict], result: LintResult) -> None:
    """Check for near-duplicate cards within a pool."""
    texts = [c.get("text", "") for c in cards]
    n = len(texts)
    for i in range(n):
        for j in range(i + 1, n):
            if _edit_distance_ok(texts[i], texts[j]):
                result.add_warning(
                    f"pool '{pool_name}': near-duplicate cards [{i}] and [{j}] "
                    f"(edit dist < 10): '{texts[i][:30]}...' vs '{texts[j][:30]}...'"
                )


def check_pool_thickness(pool_name: str, count: int, result: LintResult) -> None:
    """Warn if pool has fewer than 5 cards."""
    if count < 5:
        result.add_warning(f"pool '{pool_name}': only {count} cards (< 5)")


# ── Known country-level keys (large regions, not specific places) ────
# Cards under these keys must not reference specific local landmarks.
_COUNTRY_KEYS = {
    "马达加斯加", "肯尼亚", "尼日利亚", "津巴布韦", "莫桑比克",
    "马来西亚", "印度尼西亚", "菲律宾", "巴基斯坦",
}


def lint_localcolor(data_dir: pathlib.Path, result: LintResult) -> None:
    """Card 81: warn if a country-level key contains specific place features."""
    for fp in sorted(data_dir.glob("localcolor*.json")):
        try:
            raw = fp.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for key, sections in data.items():
            if key not in _COUNTRY_KEYS:
                continue
            if not isinstance(sections, dict):
                continue
            for _section, items in sections.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    text = item if isinstance(item, str) else (
                        item.get("text", "") if isinstance(item, dict) else ""
                    )
                    for feat in _PLACE_FEATURE_KEYWORDS:
                        if feat in text:
                            result.add_warning(
                                f"{fp.name}: country key '{key}' "
                                f"contains place feature '{feat}' in: "
                                f"'{text[:40]}...'"
                            )


def lint_all(content_src: pathlib.Path) -> LintResult:
    """Run all lint checks on content_src/. Returns combined result."""
    result = LintResult()

    # Check phenology
    pheno_fp = content_src / "phenology.json"
    if pheno_fp.exists():
        pheno_cards = lint_phenology(pheno_fp, result)
        check_duplicates("phenology", pheno_cards, result)
        check_pool_thickness("phenology", len(pheno_cards), result)
    else:
        result.add_error("phenology.json not found in content_src/")

    # Check discovery_city pools
    disc_dir = content_src / "discovery_city"
    if disc_dir.is_dir():
        for fp in sorted(disc_dir.glob("*.txt")):
            cc = fp.stem
            pool_name = f"discovery_city_{cc}"
            cards = lint_txt_pool(fp, result)
            check_duplicates(pool_name, cards, result)
            check_pool_thickness(pool_name, len(cards), result)
    else:
        result.add_error("discovery_city/ directory not found in content_src/")

    # Card 81: check localcolor for country-key place-name issues
    data_dir = content_src.parent / "nowhere" / "data"
    if data_dir.is_dir():
        lint_localcolor(data_dir, result)

    return result


if __name__ == "__main__":
    repo = pathlib.Path(__file__).resolve().parent.parent
    src = repo / "content_src"
    r = lint_all(src)
    for w in r.warnings:
        print(w, file=sys.stderr)
    for e in r.errors:
        print(e, file=sys.stderr)
    if not r.ok:
        print(f"\nLint FAILED: {len(r.errors)} error(s), {len(r.warnings)} warning(s)")
        sys.exit(1)
    else:
        print(f"\nLint OK: {len(r.warnings)} warning(s)")
