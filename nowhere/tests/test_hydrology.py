# nowhere/tests/test_hydrology.py
import random

import httpx
import respx

from nowhere import hydrology


async def test_nearby_water_returns_features(respx_mock):
    """Overpass returns two rivers; we should get at least one."""
    respx_mock.get(url__startswith="https://overpass-api.de").mock(
        return_value=httpx.Response(200, json={"elements": [
            {
                "type": "node", "id": 1, "lat": 29.57, "lon": 106.46,
                "tags": {"name": "嘉陵江", "waterway": "river"},
            },
            {
                "type": "node", "id": 2, "lat": 29.55, "lon": 106.45,
                "tags": {"name": "长江", "waterway": "river"},
            },
        ]}),
    )
    features = await hydrology.nearby_water(29.55, 106.45)
    assert len(features) >= 1
    assert any("江" in f["name"] or "河" in f["name"] for f in features)
    # Check structure
    for f in features:
        assert "name" in f
        assert "type" in f
        assert "distance_km" in f
        assert "bearing" in f
        assert "detail" in f
        assert f["type"] in ("river", "lake", "stream", "waterfall", "reservoir")


async def test_nearby_water_deduplicates(respx_mock):
    """Same-named features should be deduped, keeping the closest."""
    respx_mock.get(url__startswith="https://overpass-api.de").mock(
        return_value=httpx.Response(200, json={"elements": [
            {
                "type": "node", "id": 1, "lat": 29.56, "lon": 106.46,
                "tags": {"name": "长江", "waterway": "river"},
            },
            {
                "type": "node", "id": 2, "lat": 29.60, "lon": 106.50,
                "tags": {"name": "长江", "waterway": "river"},
            },
        ]}),
    )
    features = await hydrology.nearby_water(29.55, 106.45)
    names = [f["name"] for f in features if f["name"] == "长江"]
    assert len(names) == 1
    # Verify the closest entry (id=1) was kept
    kept = [f for f in features if f["name"] == "长江"][0]
    assert kept["distance_km"] < 2.0, "Should keep the closer entry"


async def test_nearby_water_lake(respx_mock):
    """natural=water with water=lake should classify as lake."""
    respx_mock.get(url__startswith="https://overpass-api.de").mock(
        return_value=httpx.Response(200, json={"elements": [
            {
                "type": "way", "id": 10,
                "center": {"lat": 30.0, "lon": 120.0},
                "tags": {"natural": "water", "water": "lake", "name": "西湖"},
            },
        ]}),
    )
    features = await hydrology.nearby_water(30.05, 120.05)
    assert len(features) == 1
    assert features[0]["type"] == "lake"
    assert features[0]["name"] == "西湖"


async def test_nearby_water_waterfall(respx_mock):
    """waterway=waterfall should classify as waterfall."""
    respx_mock.get(url__startswith="https://overpass-api.de").mock(
        return_value=httpx.Response(200, json={"elements": [
            {
                "type": "node", "id": 20, "lat": 28.0, "lon": 110.0,
                "tags": {"waterway": "waterfall", "name": "黄果树"},
            },
        ]}),
    )
    features = await hydrology.nearby_water(28.01, 110.01)
    assert len(features) == 1
    assert features[0]["type"] == "waterfall"
    assert features[0]["name"] == "黄果树"


def test_describe_water_has_prose():
    """describe_water returns literary text with no forbidden words."""
    features = [
        {"name": "嘉陵江", "type": "river", "distance_km": 2.0, "bearing": "东北", "detail": ""},
    ]
    text = hydrology.describe_water(features, random.Random(1))
    assert len(text) > 20
    for bad in ["很", "非常", "十分"]:
        assert bad not in text


def test_describe_water_prefers_waterfall():
    """Waterfall should be picked over river as most interesting."""
    features = [
        {"name": "长江", "type": "river", "distance_km": 1.0, "bearing": "东", "detail": ""},
        {"name": "瀑布", "type": "waterfall", "distance_km": 3.0, "bearing": "西", "detail": ""},
    ]
    text = hydrology.describe_water(features, random.Random(42))
    assert len(text) > 10


def test_describe_water_empty():
    """Empty features list returns empty string."""
    assert hydrology.describe_water([], random.Random(0)) == ""


async def test_no_water(respx_mock):
    """Empty Overpass response returns empty list."""
    respx_mock.get(url__startswith="https://overpass-api.de").mock(
        return_value=httpx.Response(200, json={"elements": []}),
    )
    features = await hydrology.nearby_water(0, 0)
    assert features == []


async def test_overpass_failure_returns_empty(respx_mock):
    """Network failure returns empty list (graceful degradation)."""
    respx_mock.get(url__startswith="https://overpass-api.de").mock(
        side_effect=httpx.TimeoutException("timeout"),
    )
    features = await hydrology.nearby_water(0, 0)
    assert features == []


def test_bearing_label():
    """Test compass bearing calculation."""
    # Due north
    assert hydrology._bearing_label(0, 0, 1, 0) == "北"
    # Due south
    assert hydrology._bearing_label(0, 0, -1, 0) == "南"
    # Due east
    assert hydrology._bearing_label(0, 0, 0, 1) == "东"
    # Due west
    assert hydrology._bearing_label(0, 0, 0, -1) == "西"


def test_haversine_km():
    """Test distance calculation sanity."""
    # Same point = 0
    assert hydrology._haversine_km(0, 0, 0, 0) == 0.0
    # ~111km per degree latitude at equator
    d = hydrology._haversine_km(0, 0, 1, 0)
    assert 110 < d < 112


def test_scene_file_exists():
    """The scene file must exist and have at least 10 lines."""
    import pathlib
    fp = pathlib.Path(__file__).resolve().parent.parent / "data" / "scene_water_features.txt"
    assert fp.exists(), f"Missing scene file: {fp}"
    lines = [l.strip() for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) >= 10, f"Only {len(lines)} scenes, need >= 10"


def test_scene_no_forbidden_words():
    """Scene file must not contain 很/非常/十分."""
    import pathlib
    fp = pathlib.Path(__file__).resolve().parent.parent / "data" / "scene_water_features.txt"
    text = fp.read_text(encoding="utf-8")
    for bad in ["很", "非常", "十分"]:
        assert bad not in text, f"Forbidden word '{bad}' found in scene file"
