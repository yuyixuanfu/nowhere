"""构建时管线: 校验 + 拆分 + 写门禁产物。

设计原则(Noel Llopis, Game Developer Magazine 2004):
  问题在构建时解决,不在运行时解决。运行时不过滤。

用法:
  python tools/build_scenes.py          # 全量构建
  python tools/build_scenes.py --check  # 只校验不输出
"""

from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter

# ── Paths ─────────────────────────────────────────────────────────────
_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SRC_DIR = _ROOT / "nowhere" / "data" / "scenes_src"
_OUT_DIR = _ROOT / "nowhere" / "data"
_BUILD_DIR = _ROOT / "build"

# ── Registries ────────────────────────────────────────────────────────
WATER_TYPES = {"river", "lake", "dock", "waterfall", "stream", "ocean", "pond"}
DISCOVERY_TYPES = {"forest", "mountain", "desert", "ocean", "polar",
                   "city", "universal", "grassland", "rainforest", "wetland"}
SEASONAL_TYPES = {"seasonal"}
VALID_TYPES = WATER_TYPES | DISCOVERY_TYPES | SEASONAL_TYPES
VALID_BIOMES = {"tundra", "desert", "coast", "mountain", "rainforest",
                "grassland", "city", "any", "forest"}
VALID_SEASONS = {"spring", "summer", "autumn", "winter"}
VALID_LAT_BANDS = {"north_temperate", "south_temperate", "tropics", "polar"}

# Ice/snow words for season contradiction check.
# "雪" excluded: used metaphorically (coral spawning "像下雪").
# "霜" excluded: used for salt crust ("盐霜"), not ice.
_ICE_WORDS = {"冰", "冻", "冰湖", "冰面", "冰川", "冰裂缝"}
# Biomes where ice content is acceptable year-round
_ICE_OK_BIOMES = {"tundra", "mountain"}
# Lat bands where ice content is acceptable in any season
_ICE_OK_LATBANDS = {"polar"}

# Biome compatibility: which types are allowed in each biome
# "any" in source means the card works everywhere
_BIOME_COMPAT: dict[str, set[str]] = {
    "tundra":     {"river", "lake", "stream", "pond"},
    "desert":     {"river", "lake", "stream", "pond"},
    "coast":      {"river", "lake", "waterfall", "stream", "dock", "ocean", "pond"},
    "mountain":   {"river", "lake", "waterfall", "stream", "pond"},
    "rainforest": {"river", "lake", "waterfall", "stream", "pond"},
    "grassland":  {"river", "lake", "stream", "pond"},
    "city":       {"river", "lake", "stream", "dock", "pond"},
    "forest":     {"river", "lake", "waterfall", "stream", "pond"},
}

# ── Forbidden words ───────────────────────────────────────────────────
_FORBIDDEN_WORDS = {"很", "非常", "十分", "巨大", "美丽"}
_VAGUE_WORDS = {"一些", "很多", "仿佛", "好像", "似乎", "有点"}

# False positive patterns: (word, suffix_that_makes_it_ok)
_FALSE_POSITIVE_CONTEXTS = {
    "十分": {"钟", "之"},  # 十分钟 = 10 minutes, 十分之一 = one tenth
}

# Inland biome forbidden water words: these terms must not appear in
# product files for biomes like desert, tundra, grassland, etc.
_INLAND_WATER_WORDS = {"瀑布", "码头", "卸货", "渡船", "渔船", "海港", "沙滩", "浪花"}
_INLAND_BIOMES = {"desert", "tundra", "grassland"}


class BuildError(Exception):
    """Raised when validation fails."""


def _validate_card(card: dict, idx: int, src_name: str) -> list[str]:
    """Validate a single card. Returns list of error strings."""
    errors: list[str] = []
    prefix = f"{src_name}[{idx}]"

    # Required fields (seasonal cards also need 'place' and 'season')
    required = ["text", "type", "biomes", "seasons", "lat_band"]
    if card.get("type") == "seasonal":
        required.extend(["place", "season"])
    for field in required:
        if field not in card:
            errors.append(f"{prefix}: missing required field '{field}'")

    text = card.get("text", "")
    ctype = card.get("type", "")
    biomes = card.get("biomes", [])
    seasons = card.get("seasons", [])
    lat_band = card.get("lat_band", [])

    # Type in registry
    if ctype and ctype not in VALID_TYPES:
        errors.append(f"{prefix}: unknown type '{ctype}' (valid: {sorted(VALID_TYPES)})")

    # Biomes in registry
    for b in biomes:
        if b not in VALID_BIOMES:
            errors.append(f"{prefix}: unknown biome '{b}' (valid: {sorted(VALID_BIOMES)})")

    # Seasons validation
    for s in seasons:
        if s not in VALID_SEASONS:
            errors.append(f"{prefix}: unknown season '{s}' (valid: {sorted(VALID_SEASONS)})")

    # Lat band validation
    for lb in lat_band:
        if lb not in VALID_LAT_BANDS:
            errors.append(f"{prefix}: unknown lat_band '{lb}' (valid: {sorted(VALID_LAT_BANDS)})")

    # Max biome count (excluding "any") — guard against mechanical copy-paste
    real_biomes = [b for b in biomes if b != "any"]
    if len(real_biomes) > 2:
        errors.append(f"{prefix}: too many biomes ({len(real_biomes)}): {real_biomes} — max 2")

    # Season contradiction: ice words in text + warm season declared or all-season
    has_ice = any(w in text for w in _ICE_WORDS)
    if has_ice:
        warm_seasons = {"spring", "summer"}
        declares_warm = bool(warm_seasons & set(seasons))
        declares_all = len(seasons) == 0  # empty = all seasons
        ice_ok_biome = bool(set(biomes) & _ICE_OK_BIOMES)
        ice_ok_latband = bool(set(lat_band) & _ICE_OK_LATBANDS)
        if (declares_warm or declares_all) and not ice_ok_biome and not ice_ok_latband:
            errors.append(
                f"{prefix}: ice words in text but seasons={seasons} ("
                f"warm/all-season without tundra/mountain/polar context)"
            )

    # Forbidden words (with false-positive context filtering)
    for word in _FORBIDDEN_WORDS:
        fidx = text.find(word)
        while fidx >= 0:
            end = fidx + len(word)
            suffix = text[end:end + 1] if end < len(text) else ""
            if word in _FALSE_POSITIVE_CONTEXTS and suffix in _FALSE_POSITIVE_CONTEXTS[word]:
                fidx = text.find(word, end)
                continue
            errors.append(f"{prefix}: forbidden word '{word}' in text")
            break

    # Vague words
    for word in _VAGUE_WORDS:
        if word in text:
            errors.append(f"{prefix}: vague word '{word}' in text")

    # Empty text
    if not text.strip():
        errors.append(f"{prefix}: empty text")

    return errors


def _load_and_validate(src_path: pathlib.Path) -> tuple[list[dict], list[str]]:
    """Load a source JSON file and validate all cards. Returns (cards, errors)."""
    if not src_path.exists():
        return [], [f"Source file not found: {src_path}"]

    try:
        data = json.loads(src_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [], [f"JSON parse error in {src_path}: {e}"]

    if not isinstance(data, list):
        return [], [f"Expected list in {src_path}, got {type(data).__name__}"]

    all_errors: list[str] = []
    src_name = src_path.stem

    for i, card in enumerate(data):
        all_errors.extend(_validate_card(card, i, src_name))

    # Duplicate detection within type
    seen_by_type: dict[str, dict[str, int]] = {}
    for i, card in enumerate(data):
        ctype = card.get("type", "")
        text = card.get("text", "")
        if ctype not in seen_by_type:
            seen_by_type[ctype] = {}
        if text in seen_by_type[ctype]:
            all_errors.append(
                f"{src_name}[{i}]: duplicate text in type '{ctype}' "
                f"(first at [{seen_by_type[ctype][text]}])"
            )
        else:
            seen_by_type[ctype][text] = i

    return data, all_errors


def _card_usable_in_biome(card: dict, biome: str) -> bool:
    """Check if a card explicitly declares the given biome (not 'any')."""
    biomes = card.get("biomes", [])
    return biome in biomes


def _check_inland_water_words(products: dict[str, list[str]]) -> list[str]:
    """Check that inland biome product files contain no water-specific words.

    Returns list of error strings.
    """
    errors: list[str] = []
    for name, lines in products.items():
        # Extract biome from product filename
        biome = None
        for prefix in ("scene_water_", "scene_discovery_"):
            if name.startswith(prefix):
                biome = name[len(prefix):]
                break
        if biome not in _INLAND_BIOMES:
            continue
        for line in lines:
            for word in _INLAND_WATER_WORDS:
                if word in line:
                    errors.append(
                        f"{name}.txt: inland biome '{biome}' contains "
                        f"water word '{word}': {line[:40]}..."
                    )
    return errors


def _build_water(cards: list[dict]) -> dict[str, list[str]]:
    """Split water cards into biome-specific product files.

    Per-type files: all cards of that type (including 'any').
    Per-biome files: cards assigned by biome affinity using _BIOME_COMPAT.
      - Cards with explicit biome declarations go to those biomes.
      - Cards with biomes=["any"] go to every biome whose compat set
        includes the card's type (e.g. waterfall cards skip desert).
    Returns dict of {filename_without_ext: [lines]}.
    """
    products: dict[str, list[str]] = {}

    # Per-type files: include ALL cards of this type
    for ctype in sorted(VALID_TYPES):
        lines = [c["text"] for c in cards if c.get("type") == ctype]
        if lines:
            products[f"scene_water_{ctype}"] = sorted(lines)

    # Per-biome files: affinity-based assignment
    for biome, compat_types in sorted(_BIOME_COMPAT.items()):
        lines = []
        for card in cards:
            card_biomes = card.get("biomes", [])
            card_type = card.get("type", "")
            if biome in card_biomes:
                # Explicitly declared for this biome
                lines.append(card["text"])
            elif "any" in card_biomes and card_type in compat_types:
                # 'any' card whose type is compatible with this biome
                lines.append(card["text"])
        if lines:
            products[f"scene_water_{biome}"] = sorted(set(lines))

    return products


def _build_discovery(cards: list[dict]) -> dict[str, list[str]]:
    """Split discovery cards into biome-specific product files.

    Per-type files: all cards of that type (including 'universal').
    Per-biome files: only cards that explicitly declare that biome.
    'any' cards go to scene_discovery_any.txt (a per-type file).
    Returns dict of {filename_without_ext: [lines]}.
    """
    # Collect only explicitly declared biomes (not 'any')
    all_biomes: set[str] = set()
    for card in cards:
        for b in card.get("biomes", []):
            if b != "any":
                all_biomes.add(b)

    products: dict[str, list[str]] = {}

    # Per-type files: include ALL cards of this type
    for dtype in sorted(set(c.get("type", "") for c in cards)):
        lines = [c["text"] for c in cards if c.get("type") == dtype]
        if lines:
            products[f"scene_discovery_{dtype}"] = sorted(lines)

    # Per-biome files: only cards that explicitly declare this biome
    for biome in sorted(all_biomes):
        lines = []
        for card in cards:
            if biome in card.get("biomes", []):
                lines.append(card["text"])
        if lines:
            products[f"scene_discovery_{biome}"] = sorted(set(lines))

    return products


def _build_seasonal(cards: list[dict]) -> dict[str, list[str]]:
    """Split seasonal cards into biome-specific product files.

    Output format per line: [place|season] text
    Returns dict of {filename_without_ext: [lines]}.
    """
    products: dict[str, list[str]] = {}

    # Collect all biomes
    for card in cards:
        biomes = card.get("biomes", ["any"])
        place = card.get("place", "")
        season = card.get("season", "")
        text = card.get("text", "")
        line = f"[{place}|{season}] {text}"
        for b in biomes:
            if b == "any":
                for biome in _BIOME_COMPAT:
                    products.setdefault(f"seasonal_{biome}", []).append(line)
            else:
                products.setdefault(f"seasonal_{b}", []).append(line)

    # Deduplicate and sort each biome pool
    for key in products:
        products[key] = sorted(set(products[key]))

    return products


def _write_products(products: dict[str, list[str]], out_dir: pathlib.Path) -> int:
    """Write product files. Returns count of files written."""
    count = 0
    for name, lines in sorted(products.items()):
        fp = out_dir / f"{name}.txt"
        fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        count += 1
    return count


def _build_scene_card_meta(all_cards: list[dict]) -> dict[str, dict]:
    """Build text→metadata mapping for runtime structured filtering.

    Only includes cards with non-empty seasons or lat_band restrictions.
    Returns {text: {"seasons": [...], "lat_band": [...], "biomes": [...]}}.
    """
    meta: dict[str, dict] = {}
    for card in all_cards:
        text = card.get("text", "")
        seasons = card.get("seasons", [])
        lat_band = card.get("lat_band", [])
        biomes = card.get("biomes", [])
        # Only store entries that have restrictions (not fully universal)
        # Card 72: include biomes in the condition check
        if seasons or lat_band or biomes:
            meta[text] = {
                "seasons": seasons,
                "lat_band": lat_band,
                "biomes": biomes,
            }
    return meta


def _write_scene_card_meta(meta: dict[str, dict], out_dir: pathlib.Path) -> None:
    """Write scene_card_meta.json for runtime filtering."""
    fp = out_dir / "scene_card_meta.json"
    fp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_audit_snapshots(all_cards: list[dict], build_dir: pathlib.Path) -> int:
    """Write audit snapshots: build/scene_snapshot_{biome}_{season}.txt.

    Each file lists all card texts that would be eligible for that
    biome+season combination. For grep-based verification only.
    Returns count of files written.
    """
    build_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    biomes_to_check = sorted(VALID_BIOMES - {"any"})
    seasons_to_check = sorted(VALID_SEASONS)

    for biome in biomes_to_check:
        for season in seasons_to_check:
            eligible: list[str] = []
            for card in all_cards:
                text = card.get("text", "")
                card_biomes = card.get("biomes", [])
                card_seasons = card.get("seasons", [])

                # Biome check: card must be "any" or include this biome
                if "any" not in card_biomes and biome not in card_biomes:
                    continue

                # Season check: card must be all-season or include this season
                if card_seasons and season not in card_seasons:
                    continue

                eligible.append(text)

            if eligible:
                fp = build_dir / f"scene_snapshot_{biome}_{season}.txt"
                fp.write_text("\n".join(sorted(eligible)) + "\n", encoding="utf-8")
                count += 1

    return count


def build(check_only: bool = False) -> tuple[int, list[str]]:
    """Main build entry point.

    Returns (files_written_or_checked, errors).
    """
    all_errors: list[str] = []
    all_products: dict[str, list[str]] = {}

    # ── Load and validate water ──
    water_path = _SRC_DIR / "water.json"
    water_cards, water_errors = _load_and_validate(water_path)
    all_errors.extend(water_errors)

    # ── Load and validate discovery ──
    disc_path = _SRC_DIR / "discovery.json"
    disc_cards, disc_errors = _load_and_validate(disc_path)
    all_errors.extend(disc_errors)

    # ── Load and validate seasonal ──
    seasonal_path = _SRC_DIR / "seasonal.json"
    seasonal_cards: list[dict] = []
    seasonal_errors: list[str] = []
    if seasonal_path.exists():
        seasonal_cards, seasonal_errors = _load_and_validate(seasonal_path)
        all_errors.extend(seasonal_errors)

    if all_errors:
        return 0, all_errors

    # ── Build products ──
    water_products = _build_water(water_cards)
    disc_products = _build_discovery(disc_cards)
    seasonal_products = _build_seasonal(seasonal_cards)
    all_products.update(water_products)
    all_products.update(disc_products)
    all_products.update(seasonal_products)

    # ── Gate: inland biome water word check ──
    inland_errors = _check_inland_water_words(all_products)
    all_errors.extend(inland_errors)
    if all_errors:
        return 0, all_errors

    # ── Build scene_card_meta (text → structured conditions) ──
    all_cards = water_cards + disc_cards + seasonal_cards
    card_meta = _build_scene_card_meta(all_cards)

    if check_only:
        # Print summary
        _print_summary(water_cards, disc_cards, seasonal_cards, all_products)
        print(f"Scene card meta entries: {len(card_meta)}")
        return len(all_products), []

    # ── Write products ──
    count = _write_products(all_products, _OUT_DIR)

    # ── Write scene_card_meta.json ──
    _write_scene_card_meta(card_meta, _OUT_DIR)

    # ── Write audit snapshots ──
    snapshot_count = _write_audit_snapshots(all_cards, _BUILD_DIR)

    _print_summary(water_cards, disc_cards, seasonal_cards, all_products)
    print(f"Scene card meta entries: {len(card_meta)}")
    print(f"Audit snapshots written: {snapshot_count}")
    return count, []


def _print_summary(water: list[dict], disc: list[dict],
                   seasonal: list[dict], products: dict[str, list[str]]) -> None:
    """Print build summary."""
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"=== Build Scenes Summary ===")
    print(f"Water source cards: {len(water)}")
    wc = Counter(c.get("type", "?") for c in water)
    for t in sorted(wc):
        print(f"  {t}: {wc[t]}")
    print(f"Discovery source cards: {len(disc)}")
    dc = Counter(c.get("type", "?") for c in disc)
    for t in sorted(dc):
        print(f"  {t}: {dc[t]}")
    if seasonal:
        print(f"Seasonal source cards: {len(seasonal)}")
        sc = Counter(c.get("place", "?") for c in seasonal)
        for p in sorted(sc):
            print(f"  {p}: {sc[p]}")
    print(f"Product files: {len(products)}")
    for name in sorted(products):
        print(f"  {name}.txt: {len(products[name])} lines")


def main() -> None:
    """CLI entry point."""
    sys.stdout.reconfigure(encoding="utf-8")
    check_only = "--check" in sys.argv

    count, errors = build(check_only=check_only)

    if errors:
        print(f"\n=== ERRORS ({len(errors)}) ===", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    if check_only:
        print(f"\nCheck passed. {count} product files would be generated.")
    else:
        print(f"\nBuild complete. {count} product files written.")


if __name__ == "__main__":
    main()
