"""Unified Card schema -- the constitution of the card system.

Five data sources, one Card dataclass:
  localcolor  -- {place: {物产: [str], ...}} + regional JSON files
  humanities  -- {places: {place: {事件: [...], ...}}}
  encounters  -- [tag] text lines in encounters.txt
  people      -- {place: {person, sight, lines, ...}}
  errands     -- [{sender, recipient, hint, text}]

Each source has a parser in ``_parse_*`` that returns ``list[Card]``.
``load_all()`` aggregates them.  ``select()`` does condition filtering +
weighted random pick.

The Card abstraction lives here; modules (localcolor, humanities, etc.)
consume it internally but keep their public API unchanged.
"""

from __future__ import annotations

import json
import pathlib
import random
from dataclasses import dataclass, field
from typing import Any

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"


# ── Card dataclass ──────────────────────────────────────────────────


@dataclass
class Card:
    """Unified card representation.

    ``id``    -- "{place}/{category}/{index}" (existing key convention)
    ``kind``  -- localcolor | humanities | encounter | people | errand
    ``text``  -- main display text
    ``conditions`` -- optional filters: place, biome, hours, months, weekday,
                season, region
    ``effect`` -- "seen" (default, drawn once), "sticky" (repeatable),
                 "evolve" (trace chain, stage progression)
    ``meta``  -- kind-specific fields (year, name, person, knows, etc.)
    """

    id: str
    kind: str
    text: str
    conditions: dict = field(default_factory=dict)
    effect: str = "seen"
    meta: dict = field(default_factory=dict)


# ── Loaders ─────────────────────────────────────────────────────────


def load_localcolor(data_dir: pathlib.Path | None = None) -> list[Card]:
    """Load localcolor cards from main + regional JSON files.

    Returns cards for ALL categories including 节律.
    """
    d = data_dir or _DATA_DIR
    main_file = d / "localcolor.json"
    base: dict = json.loads(main_file.read_text("utf-8")) if main_file.exists() else {}

    regional_files = [
        "localcolor_china.json",
        "localcolor_japan_korea_sea.json",
        "localcolor_americas_africa_oceania.json",
        "localcolor_natural.json",
        "localcolor_europe_middleeast.json",
    ]
    for fname in regional_files:
        p = d / fname
        if not p.exists():
            continue
        regional = json.loads(p.read_text("utf-8"))
        for k, v in regional.items():
            if k not in base:
                base[k] = v

    cards: list[Card] = []
    for place, entry in base.items():
        # Regular categories (物产, 声音, 痕迹, 植被, 美食)
        for cat in ("物产", "声音", "痕迹", "植被", "美食"):
            for i, text in enumerate(entry.get(cat, [])):
                cid = f"{place}/{cat}/{i}"
                weight = 3.0 if cat == "美食" else 1.0
                cards.append(Card(
                    id=cid,
                    kind="localcolor",
                    text=text,
                    conditions={"place": place},
                    meta={"category": cat, "weight": weight},
                ))

        # 节律 (rhythm) cards
        for i, r in enumerate(entry.get("节律", [])):
            if isinstance(r, str):
                cid = f"{place}/节律/{i}"
                cards.append(Card(
                    id=cid,
                    kind="localcolor",
                    text=r,
                    conditions={"place": place},
                    meta={"category": "节律", "weight": 1.0},
                ))
            else:
                cid = f"{place}/节律/{i}"
                cond: dict[str, Any] = {"place": place}
                if "hours" in r:
                    cond["hours"] = r["hours"]
                if "months" in r:
                    cond["months"] = r["months"]
                if "weekdays" in r:
                    cond["weekday"] = r["weekdays"]
                cards.append(Card(
                    id=cid,
                    kind="localcolor",
                    text=r["text"],
                    conditions=cond,
                    meta={"category": "节律", "weight": 1.0},
                ))

    return cards


def load_humanities(data_dir: pathlib.Path | None = None) -> list[Card]:
    """Load humanities cards from main + regional JSON files."""
    d = data_dir or _DATA_DIR
    main_file = d / "humanities.json"
    raw = json.loads(main_file.read_text("utf-8")) if main_file.exists() else {}
    places: dict = raw.get("places", {})

    regional_files = [
        "humanities_films.json",
        "humanities_historical.json",
    ]
    for fname in regional_files:
        p = d / fname
        if not p.exists():
            continue
        regional = json.loads(p.read_text("utf-8"))
        entries = regional.get("places", regional) if isinstance(regional, dict) else regional
        for k, v in entries.items():
            if k.startswith("_"):
                continue
            if k not in places:
                places[k] = v

    cards: list[Card] = []
    for place, entry in places.items():
        for cat in ("事件", "人物", "作品"):
            for i, card_data in enumerate(entry.get(cat, [])):
                cid = f"{place}/{cat}/{i}"
                meta: dict[str, Any] = {"category": cat}
                # Carry over all non-text fields as meta (name, year, title, creator, ...)
                for k, v in card_data.items():
                    if k != "text":
                        meta[k] = v
                cards.append(Card(
                    id=cid,
                    kind="humanities",
                    text=card_data.get("text", ""),
                    conditions={"place": place},
                    meta=meta,
                ))

    return cards


def load_encounters(data_dir: pathlib.Path | None = None) -> list[Card]:
    """Load encounter cards from encounters.txt."""
    d = data_dir or _DATA_DIR
    fp = d / "encounters.txt"
    cards: list[Card] = []
    if not fp.exists():
        return cards

    counters: dict[str, int] = {}
    for line in fp.read_text("utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "---":
            continue
        if stripped.startswith("[") and "]" in stripped:
            bracket_end = stripped.index("]")
            tag = stripped[1:bracket_end].lower().strip()
            text = stripped[bracket_end + 1:].strip()
            if text.startswith("[") and text.endswith("]"):
                continue
            if not text:
                continue
            i = counters.get(tag, 0)
            counters[tag] = i + 1
            cid = f"encounter/{tag}/{i}"
            cards.append(Card(
                id=cid,
                kind="encounter",
                text=text,
                conditions={"region": tag},
                meta={"tag": tag},
            ))

    return cards


def load_people(data_dir: pathlib.Path | None = None) -> list[Card]:
    """Load people cards from people_seed.json."""
    d = data_dir or _DATA_DIR
    fp = d / "people_seed.json"
    if not fp.exists():
        return []
    data = json.loads(fp.read_text("utf-8"))
    cards: list[Card] = []
    for place, entry in data.items():
        person = entry.get("person", "")
        cid = f"{place}/{person}"
        cond: dict[str, Any] = {"place": place}
        months = entry.get("months")
        if months:
            cond["months"] = months
        cards.append(Card(
            id=cid,
            kind="people",
            text=entry.get("sight", ""),
            conditions=cond,
            meta={
                "person": person,
                "where": entry.get("where", ""),
                "lines": entry.get("lines", []),
                "knows": entry.get("knows", {}),
            },
        ))
    return cards


def load_errands(data_dir: pathlib.Path | None = None) -> list[Card]:
    """Load errand/letter cards from errands_letters.json."""
    d = data_dir or _DATA_DIR
    fp = d / "errands_letters.json"
    if not fp.exists():
        return []
    data = json.loads(fp.read_text("utf-8"))
    cards: list[Card] = []
    for i, letter in enumerate(data):
        cid = f"errand/letter/{i}"
        cards.append(Card(
            id=cid,
            kind="errand",
            text=letter.get("text", ""),
            meta={
                "sender": letter.get("sender", ""),
                "recipient": letter.get("recipient", ""),
                "hint": letter.get("hint", ""),
            },
        ))
    return cards


def load_all(data_dir: pathlib.Path | None = None) -> list[Card]:
    """Load all card sources and return a unified Card list."""
    cards: list[Card] = []
    cards.extend(load_localcolor(data_dir))
    cards.extend(load_humanities(data_dir))
    cards.extend(load_encounters(data_dir))
    cards.extend(load_people(data_dir))
    cards.extend(load_errands(data_dir))
    return cards


# ── Condition matching ──────────────────────────────────────────────


def matches_conditions(card: Card, ctx: dict) -> bool:
    """Check whether a card's conditions are satisfied by context.

    Context keys: place, biome, hours, month, weekday, season, region.
    All optional; missing context key means "don't filter on this".
    """
    cond = card.conditions

    # Place filter
    place = cond.get("place")
    if place and ctx.get("place") and place != ctx["place"]:
        return False

    # Biome filter
    biome = cond.get("biome")
    if biome and ctx.get("biome"):
        ctx_biome = ctx["biome"].lower()
        if isinstance(biome, str) and biome.lower() != ctx_biome:
            return False

    # Hours filter: cond["hours"] = [start, end) range
    hours = cond.get("hours")
    if hours and ctx.get("hours") is not None:
        h = ctx["hours"]
        if not (hours[0] <= h < hours[1]):
            return False

    # Month filter: cond["months"] = list of valid months
    months = cond.get("months")
    if months and ctx.get("month") is not None:
        if ctx["month"] not in months:
            return False

    # Weekday filter: cond["weekday"] = list of valid weekdays (0=Mon, 6=Sun)
    weekday = cond.get("weekday")
    if weekday is not None and ctx.get("weekday") is not None:
        if ctx["weekday"] not in weekday:
            return False

    # Region filter (for encounters)
    region = cond.get("region")
    if region and ctx.get("region") and region != ctx["region"]:
        return False

    return True


# ── Selection ───────────────────────────────────────────────────────


def select(
    pool: list[Card],
    ctx: dict,
    rng: random.Random,
    seen: set[str] | None = None,
    k: int = 1,
) -> list[Card]:
    """Condition-filter + weighted random pick from a card pool.

    Args:
        pool: candidate cards.
        ctx: context dict for condition matching.
        rng: random number generator.
        seen: set of already-seen card IDs (cards with effect="seen" are skipped).
        k: number of cards to draw.

    Returns:
        Up to *k* cards that pass conditions and aren't in *seen*.
    """
    if seen is None:
        seen = set()

    eligible: list[tuple[Card, float]] = []
    for card in pool:
        if card.effect == "seen" and card.id in seen:
            continue
        if not matches_conditions(card, ctx):
            continue
        w = card.meta.get("weight", 1.0)
        eligible.append((card, w))

    if not eligible:
        return []

    # Meal-time food bonus (same logic as localcolor.draw)
    meal_time = ctx.get("hours") is not None and (
        6 <= ctx["hours"] < 9 or 11 <= ctx["hours"] < 13 or 17 <= ctx["hours"] < 21
    )
    if not meal_time:
        eligible = [
            (c, w if c.meta.get("category") != "美食" else 1.0)
            for c, w in eligible
        ]

    # Weighted sampling without replacement
    results: list[Card] = []
    remaining = list(eligible)
    for _ in range(min(k, len(remaining))):
        total = sum(w for _, w in remaining)
        r = rng.uniform(0, total)
        for idx, (card, w) in enumerate(remaining):
            r -= w
            if r <= 0:
                results.append(card)
                remaining.pop(idx)
                break

    return results


# ── Validation ──────────────────────────────────────────────────────


def validate_card(card: Card) -> bool:
    """Check that a Card has required fields and valid kind."""
    if not card.id:
        return False
    if card.kind not in ("localcolor", "humanities", "encounter", "people", "errand"):
        return False
    if not card.text:
        return False
    return True
