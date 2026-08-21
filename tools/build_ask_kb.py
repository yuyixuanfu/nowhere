#!/usr/bin/env python3
"""Offline builder: extract Wikipedia ZIM first-paragraphs for ask-able names.

Run once to produce ``nowhere/data/ask_kb.json``.  The resulting JSON is
checked in so that runtime code never needs to open the 3 GB ZIM file.

Usage (from repo root)::

    python tools/build_ask_kb.py            # default ZIM path
    python tools/build_ask_kb.py /path/to.zim  # custom ZIM path
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.parse

# ── paths ─────────────────────────────────────────────────────────────
_REPO = pathlib.Path(__file__).resolve().parent.parent
_DATA = _REPO / "nowhere" / "data"
_ZIM_DEFAULT = _DATA / "packs" / "wikipedia_zh_mini.zim"
_OUT = _DATA / "ask_kb.json"

_NAMESPACE = "C"  # articles namespace
_MAX_EXTRACT = 300  # chars per entry


# ── helpers ───────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Extract plain text from the first non-empty <p> block."""
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    for m in re.finditer(r"<p[^>]*>(.*?)</p>", html, re.DOTALL):
        text = m.group(1)
        text = re.sub(r"<sup[^>]*>.*?</sup>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text
    return ""


def _t2s(text: str) -> str:
    """Traditional to Simplified Chinese."""
    try:
        import opencc
        converter = opencc.OpenCC("t2s")
        return converter.convert(text)
    except Exception:
        return text


def _truncate(text: str, max_len: int = _MAX_EXTRACT) -> str:
    """Cut to <= max_len chars at sentence boundary."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    for sep in ("。", "．", ". ", "."):
        idx = cut.rfind(sep)
        if idx > 50:
            return cut[: idx + 1]
    return cut + "..."


# ── collect all searchable names ──────────────────────────────────────

def _collect_names() -> list[str]:
    """Gather place names + humanities person/event names."""
    names: set[str] = set()

    # 1) explorable_index place names (skip ascii-only generic keys)
    ei_path = _DATA / "explorable_index.json"
    if ei_path.exists():
        ei = json.loads(ei_path.read_text(encoding="utf-8"))
        for name in ei.get("places", {}):
            # skip purely ascii keys like "africa", "americas", "art", etc.
            if not re.search(r"[一-鿿]", name):
                continue
            names.add(name)

    # 2) humanities files: person names + event names
    for fname in ("humanities.json", "humanities_historical.json", "humanities_films.json"):
        fp = _DATA / fname
        if not fp.exists():
            continue
        raw = json.loads(fp.read_text(encoding="utf-8"))
        places = raw.get("places", raw)
        for _place, entry in places.items():
            if not isinstance(entry, dict):
                continue
            for evt in entry.get("事件", []):
                if isinstance(evt, dict) and "name" in evt:
                    names.add(evt["name"])
            for per in entry.get("人物", []):
                if isinstance(per, dict) and "name" in per:
                    names.add(per["name"])
            # 作品 titles are also ask-able
            for work in entry.get("作品", []):
                if isinstance(work, dict):
                    t = work.get("title") or work.get("name")
                    if t:
                        names.add(t)

    return sorted(names)


# ── ZIM extraction ────────────────────────────────────────────────────

def _try_zim(zim, title: str) -> str | None:
    """Look up *title* in ZIM, return first-paragraph text or None."""
    try:
        art = zim.get_article_by_url(_NAMESPACE, title)
    except Exception:
        return None
    if art is None or art.data is None:
        return None
    html = art.data.decode("utf-8", errors="replace") if isinstance(art.data, bytes) else art.data
    text = _strip_html(html)
    return text if text else None


def _lookup(zim, title: str) -> str | None:
    """Multi-strategy ZIM lookup (mirrors old knowledge.py logic)."""
    # direct
    text = _try_zim(zim, title)
    if text:
        return text

    # URL-encoded
    encoded = urllib.parse.quote(title, safe="")
    if encoded != title:
        text = _try_zim(zim, encoded)
        if text:
            return text

    # underscores
    if " " in title:
        text = _try_zim(zim, title.replace(" ", "_"))
        if text:
            return text

    # disambiguation suffixes
    for suffix in (" (地理)", " (地质学)", " (消歧義)", "地貌", "地形"):
        text = _try_zim(zim, title + suffix)
        if text:
            return text

    # suggest
    try:
        matches = zim.suggest(title)
        if matches:
            for mt in matches[:3]:
                if mt and mt != title:
                    text = _try_zim(zim, mt)
                    if text:
                        return text
    except Exception:
        pass

    return None


# ── main ──────────────────────────────────────────────────────────────

def main() -> None:
    zim_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else _ZIM_DEFAULT
    if not zim_path.exists():
        print(f"ZIM not found: {zim_path}", file=sys.stderr)
        sys.exit(1)

    names = _collect_names()
    print(f"[build_ask_kb] {len(names)} names to look up")

    # open ZIM
    from zimply.zimply import ZIMFile
    zim = ZIMFile(str(zim_path), encoding="utf-8")

    kb: dict[str, str] = {}
    hit = 0
    for i, name in enumerate(names):
        raw = _lookup(zim, name)
        if raw:
            simplified = _t2s(raw)
            entry = _truncate(simplified, _MAX_EXTRACT)
            kb[name] = entry
            hit += 1
        if (i + 1) % 100 == 0:
            print(f"  ... {i+1}/{len(names)} ({hit} hits)")

    print(f"[build_ask_kb] done: {hit}/{len(names)} entries")

    _OUT.write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[build_ask_kb] wrote {_OUT}  ({_OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
