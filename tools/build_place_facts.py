#!/usr/bin/env python3
"""Offline builder: extract Wikipedia ZIM paragraphs as raw facts for places.

For each place in EXPLORABLE_PLACES.md, extracts relevant text from
the offline Wikipedia ZIM and saves to ``drafts/{place}_facts.md``.

The mini ZIM contains only first-paragraph summaries. We extract the
article text, try multiple URL variants (place+市/县/区), and use
the ZIM suggest() for fallback matches.

Usage (from repo root)::

    python tools/build_place_facts.py              # default ZIM path, all places
    python tools/build_place_facts.py --place 北京  # single place
    python tools/build_place_facts.py --limit 10    # first 10 places only
    python tools/build_place_facts.py /path/to.zim  # custom ZIM path
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import urllib.parse

# ── paths ─────────────────────────────────────────────────────────────
_REPO = pathlib.Path(__file__).resolve().parent.parent
_DATA = _REPO / "nowhere" / "data"
_DRAFTS = _REPO / "drafts"
_ZIM_DEFAULT = _DATA / "packs" / "wikipedia_zh_mini.zim"
_EP_FILE = _REPO / "nowhere" / "EXPLORABLE_PLACES.md"

_NAMESPACE = "C"  # articles namespace


# ── helpers ───────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Extract plain text from HTML, cleaning infoboxes and references."""
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(r"<sup[^>]*>.*?</sup>", "", html, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_article_text(html: str) -> str:
    """Extract meaningful article text, skipping infobox data.

    For the mini ZIM, the article is usually:
    1. Infobox metadata (skip)
    2. First paragraph of the article (keep)
    3. References/copyright (skip)
    """
    # Try to extract from <p> blocks first
    paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
    texts = []
    for p in paras:
        text = re.sub(r"<sup[^>]*>.*?</sup>", "", p, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        # Skip very short or reference-like text
        if len(text) > 30 and "Issued from Wikipedia" not in text:
            texts.append(text)

    if texts:
        return " ".join(texts)

    # Fallback: strip all HTML
    full = _strip_html(html)
    # Try to find the actual article text after infobox
    for pattern in [
        r"(?:位于|坐落于|坐落在|地处|是).{20,}",
    ]:
        m = re.search(pattern, full)
        if m:
            start = m.start()
            end = min(len(full), start + 1000)
            text = full[start:end]
            for sep in ("。", "．"):
                idx = text.rfind(sep)
                if idx > 100:
                    text = text[: idx + 1]
                    break
            return text

    # Last resort
    if len(full) > 1500:
        m = re.search(r"[一-鿿]{5,}", full)
        if m:
            start = m.start()
            full = full[start : start + 1000]
    return full[:1000] if full else ""


def _t2s(text: str) -> str:
    """Traditional to Simplified Chinese."""
    try:
        import opencc
        converter = opencc.OpenCC("t2s")
        return converter.convert(text)
    except Exception:
        return text


def _read_explorable_places() -> list[str]:
    """Read place names from EXPLORABLE_PLACES.md."""
    text = _EP_FILE.read_text(encoding="utf-8")
    places = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("- "):
            places.append(line[2:].strip())
    return places


# ── ZIM lookup ────────────────────────────────────────────────────────

def _try_zim(zim, title: str) -> str | None:
    """Look up *title* in ZIM, return HTML or None."""
    try:
        art = zim.get_article_by_url(_NAMESPACE, title)
    except Exception:
        return None
    if art is None or art.data is None:
        return None
    html = art.data.decode("utf-8", errors="replace") if isinstance(art.data, bytes) else art.data
    return html


def _lookup_article(zim, title: str) -> str | None:
    """Multi-strategy ZIM lookup for a place, return HTML or None."""
    # direct
    html = _try_zim(zim, title)
    if html:
        return html

    # URL-encoded
    encoded = urllib.parse.quote(title, safe="")
    if encoded != title:
        html = _try_zim(zim, encoded)
        if html:
            return html

    # underscores
    if " " in title:
        html = _try_zim(zim, title.replace(" ", "_"))
        if html:
            return html

    # disambiguation suffixes
    for suffix in ("市", "县", "区", " (地理)", " (消歧義)"):
        html = _try_zim(zim, title + suffix)
        if html:
            return html

    # suggest
    try:
        matches = zim.suggest(title)
        if matches:
            for mt in matches[:5]:
                if mt and mt != title:
                    html = _try_zim(zim, mt)
                    if html:
                        return html
    except Exception:
        pass

    return None


# ── main ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract place facts from Wikipedia ZIM")
    parser.add_argument("zim_path", nargs="?", default=str(_ZIM_DEFAULT), help="Path to ZIM file")
    parser.add_argument("--place", help="Extract facts for a single place only")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of places to process")
    parser.add_argument("--skip-existing", action="store_true", help="Skip places that already have facts files")
    args = parser.parse_args()

    zim_path = pathlib.Path(args.zim_path)
    if not zim_path.exists():
        print(f"ZIM not found: {zim_path}", file=sys.stderr)
        sys.exit(1)

    _DRAFTS.mkdir(parents=True, exist_ok=True)

    if args.place:
        places = [args.place]
    else:
        places = _read_explorable_places()

    if args.limit > 0:
        places = places[: args.limit]

    print(f"[build_place_facts] {len(places)} places to process")

    # open ZIM
    from zimply.zimply import ZIMFile
    zim = ZIMFile(str(zim_path), encoding="utf-8")

    hit = 0
    miss = 0
    skipped = 0
    for i, place in enumerate(places):
        out_path = _DRAFTS / f"{place}_facts.md"

        if args.skip_existing and out_path.exists():
            skipped += 1
            continue

        html = _lookup_article(zim, place)
        if html:
            text = _extract_article_text(html)
            text = _t2s(text)
            if text and len(text) > 20:
                md = f"# {place} — 实据提取\n\n来源: wikipedia_zh_mini.zim / {place}\n\n"
                md += f"## 概述\n\n> {text}\n\n"
                md += f"---\n共 1 条实据段落\n"
                out_path.write_text(md, encoding="utf-8")
                hit += 1
            else:
                miss += 1
                out_path.write_text(
                    f"# {place} — 实据提取\n\n来源: wikipedia_zh_mini.zim\n\n"
                    f"> (ZIM 条目为空或过短)\n\n---\n共 0 条实据段落\n",
                    encoding="utf-8",
                )
        else:
            miss += 1
            out_path.write_text(
                f"# {place} — 实据提取\n\n来源: wikipedia_zh_mini.zim\n\n"
                f"> (ZIM 中未找到此条目)\n\n---\n共 0 条实据段落\n",
                encoding="utf-8",
            )

        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(places)}] ({hit} hits, {miss} misses)")

    print(f"\n[build_place_facts] done: {hit} hits, {miss} misses, {skipped} skipped")
    print(f"[build_place_facts] wrote to {_DRAFTS}")


if __name__ == "__main__":
    main()
