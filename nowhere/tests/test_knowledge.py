"""Tests for the local knowledge base integration in knowledge.py."""

import asyncio

from nowhere import knowledge


def test_local_kb_country():
    r = asyncio.run(knowledge.about(36.2, 138.2, "日本"))
    assert r is not None
    assert "日本" in r["title"]
    assert len(r["extract"]) > 10
    assert r["source"] == "local_kb"


def test_local_kb_city():
    r = asyncio.run(knowledge.about(35.68, 139.69, "东京"))
    assert r is not None
    assert "东京" in r["title"]
    assert r["source"] == "local_kb"


def test_local_kb_fuzzy():
    r = asyncio.run(knowledge.about(48.85, 2.35, "巴黎"))
    assert r is not None
    assert r["source"] == "local_kb"


def test_local_kb_not_found():
    """A completely made-up topic should not crash."""
    r = asyncio.run(knowledge.about(0.0, 0.0, "zzz_nonexistent_place_zzz"))
    # May return None or a ZIM result; should not raise
    assert r is None or isinstance(r, dict)
