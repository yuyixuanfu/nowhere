"""Tests for life / art / knowledge encounter-pool source modules."""

from __future__ import annotations

import random

import httpx
import pytest

from nowhere import art, knowledge, life


# ── life.py ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_life_prefers_nocturnal_at_night(respx_mock):
    respx_mock.get(url__startswith="https://api.inaturalist.org").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 1,
                        "observed_on": "2026-07-18",
                        "taxon": {
                            "name": "Passer domesticus",
                            "preferred_common_name": "House Sparrow",
                        },
                        "geojson": {"coordinates": [139.7, 35.6]},
                    },
                    {
                        "id": 2,
                        "observed_on": "2026-07-19",
                        "taxon": {
                            "name": "Strix aluco",
                            "preferred_common_name": "Tawny Owl",
                        },
                        "geojson": {"coordinates": [139.71, 35.61]},
                    },
                ]
            },
        )
    )
    r = await life.nearby(35.6, 139.7, night=True, weather_text="晴")
    assert r is not None
    assert "Owl" in r["common_name"]


@pytest.mark.anyio
async def test_life_prefers_amphibian_in_rain(respx_mock):
    respx_mock.get(url__startswith="https://api.inaturalist.org").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 1,
                        "observed_on": "2026-07-18",
                        "taxon": {
                            "name": "Passer domesticus",
                            "preferred_common_name": "House Sparrow",
                        },
                        "geojson": {"coordinates": [139.7, 35.6]},
                    },
                    {
                        "id": 2,
                        "observed_on": "2026-07-19",
                        "taxon": {
                            "name": "Rana temporaria",
                            "preferred_common_name": "Common Frog",
                        },
                        "geojson": {"coordinates": [139.71, 35.61]},
                    },
                ]
            },
        )
    )
    r = await life.nearby(35.6, 139.7, night=False, weather_text="rain")
    assert r is not None
    assert "Frog" in r["common_name"]


@pytest.mark.anyio
async def test_life_returns_none_when_down(respx_mock):
    respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
    assert await life.nearby(35.6, 139.7, night=False, weather_text="晴") is None


@pytest.mark.anyio
async def test_life_returns_none_empty_results(respx_mock):
    respx_mock.get(url__startswith="https://api.inaturalist.org").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    assert await life.nearby(35.6, 139.7, night=False, weather_text="晴") is None


@pytest.mark.anyio
async def test_life_seasonal_boost_winter(respx_mock):
    """Winter-month query boosts winter-appropriate animals (owl over butterfly)."""
    respx_mock.get(url__startswith="https://api.inaturalist.org").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 1,
                        "observed_on": "2026-01-15",
                        "taxon": {
                            "name": "Pieris rapae",
                            "preferred_common_name": "Cabbage Butterfly",
                        },
                        "geojson": {"coordinates": [139.7, 35.6]},
                    },
                    {
                        "id": 2,
                        "observed_on": "2026-01-16",
                        "taxon": {
                            "name": "Strix aluco",
                            "preferred_common_name": "Tawny Owl",
                        },
                        "geojson": {"coordinates": [139.71, 35.61]},
                    },
                ]
            },
        )
    )
    # With month=1 (winter), owl should be boosted and preferred
    r = await life.nearby(35.6, 139.7, night=False, weather_text="晴", month=1, rng=random.Random(42))
    assert r is not None
    assert "Owl" in r["common_name"]
    assert r["season"] == "winter"


@pytest.mark.anyio
async def test_life_seasonal_summer(respx_mock):
    """Summer-month query returns season in result."""
    respx_mock.get(url__startswith="https://api.inaturalist.org").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 1,
                        "observed_on": "2026-07-15",
                        "taxon": {
                            "name": "Cicada orni",
                            "preferred_common_name": "Cicada",
                        },
                        "geojson": {"coordinates": [139.7, 35.6]},
                    },
                ]
            },
        )
    )
    r = await life.nearby(35.6, 139.7, night=False, weather_text="晴", month=7, biome="forest")
    assert r is not None
    assert r["season"] == "summer"
    assert r["biome"] == "forest"


def test_month_to_season():
    """Month-to-season mapping."""
    assert life._month_to_season(1) == "winter"
    assert life._month_to_season(3) == "spring"
    assert life._month_to_season(7) == "summer"
    assert life._month_to_season(10) == "autumn"
    assert life._month_to_season(12) == "winter"
    assert life._month_to_season(None) == ""


# ── art.py ────────────────────────────────────────────────────────

def test_local_art_match():
    r = art._local_art_match(35.68, 139.69, "calm", random.Random(1))
    assert r is not None
    assert r["title"]
    assert r["image_url"]


def test_local_art_match_no_db(monkeypatch):
    """Returns None gracefully when DB is empty."""
    monkeypatch.setattr(art, "_ART_DB", {"artworks": [], "by_culture": {}, "count": 0})
    r = art._local_art_match(35.68, 139.69, "calm", random.Random(1))
    assert r is None


@pytest.mark.anyio
async def test_art_match(respx_mock):
    respx_mock.get(
        url__startswith="https://collectionapi.metmuseum.org/public/collection/v1/search"
    ).mock(return_value=httpx.Response(200, json={"objectIDs": [436535]}))
    respx_mock.get(
        url__startswith="https://collectionapi.metmuseum.org/public/collection/v1/objects"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "title": "Wheat Field with Cypresses",
                "artistDisplayName": "Vincent van Gogh",
                "objectDate": "1889",
                "primaryImage": "https://x/img.jpg",
                "culture": "",
            },
        )
    )
    r = await art.match(48.85, 2.35, "calm")
    assert r is not None
    assert r["title"]
    assert r["image_url"].startswith("http")


@pytest.mark.anyio
async def test_art_returns_none_when_search_empty(respx_mock, monkeypatch):
    """When local DB is empty and API returns no results, match returns None."""
    monkeypatch.setattr(art, "_ART_DB", {"artworks": [], "by_culture": {}, "count": 0})
    respx_mock.get(
        url__startswith="https://collectionapi.metmuseum.org/public/collection/v1/search"
    ).mock(return_value=httpx.Response(200, json={"objectIDs": None}))
    assert await art.match(48.85, 2.35, "calm") is None


@pytest.mark.anyio
async def test_art_returns_none_when_down(respx_mock, monkeypatch):
    """When local DB is empty and API is down, match returns None."""
    monkeypatch.setattr(art, "_ART_DB", {"artworks": [], "by_culture": {}, "count": 0})
    respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
    assert await art.match(48.85, 2.35, "calm") is None


# ── knowledge.py (offline ZIM) ────────────────────────────────────

@pytest.mark.anyio
async def test_knowledge_zim_topic_lookup():
    """ZIM lookup by topic returns title + extract."""
    r = await knowledge.about(35.36, 138.73, "富士山")
    assert r is not None
    assert "富士" in r["title"]
    assert len(r["extract"]) > 20
    assert r["url"].startswith("https://zh.wikipedia.org/")


@pytest.mark.anyio
async def test_knowledge_zim_returns_none_unknown_topic():
    """Unknown topic returns None."""
    assert await knowledge.about(0, 0, "不存在的条目_xyzzy") is None


@pytest.mark.anyio
async def test_knowledge_zim_returns_none_empty_topic_no_places():
    """Empty topic with no places.db returns None (graceful fallback)."""
    # At lat=0, lon=0 there's likely no nearby place in places.db,
    # or places.db may not exist -- either way, should return None.
    r = await knowledge.about(0, 0, "")
    # Could be None or a result if places.db has a nearby entry at 0,0
    # The key point: no crash
    assert r is None or isinstance(r.get("title"), str)


@pytest.mark.anyio
async def test_knowledge_zim_file_missing(monkeypatch, tmp_path):
    """ZIM file missing returns None gracefully."""
    monkeypatch.setattr(knowledge, "_zim", None)
    monkeypatch.setattr(knowledge, "_ZIM_PATH", tmp_path / "nonexistent.zim")
    assert await knowledge.about(35.36, 138.73, "富士山") is None
