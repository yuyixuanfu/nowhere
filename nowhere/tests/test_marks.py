"""Tests for marks (bookmarks) and geocode modules."""

import httpx
import respx

from nowhere import geocode, marks


# ── marks ──────────────────────────────────────────────────────────


def test_marks_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    marks.save("和她来过的地方", 39.47, 75.99, "喀什,凌晨四点")
    m = marks.get("和她来过的地方")
    assert m and abs(m["lat"] - 39.47) < 1e-6 and m["note"] == "喀什,凌晨四点"
    assert len(marks.all()) == 1


def test_marks_save_duplicate_raises(tmp_path, monkeypatch):
    """Saving a duplicate name without overwrite=True should raise ValueError."""
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    marks.save("x", 1.0, 2.0)
    import pytest
    with pytest.raises(ValueError, match="已经标过了"):
        marks.save("x", 3.0, 4.0)


def test_marks_save_duplicate_overwrite(tmp_path, monkeypatch):
    """Saving with overwrite=True should succeed and update coords."""
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    marks.save("x", 1.0, 2.0)
    marks.save("x", 3.0, 4.0, overwrite=True)
    m = marks.get("x")
    assert m["lat"] == 3.0
    assert m["lon"] == 4.0
    assert len(marks.all()) == 1


def test_marks_get_returns_original(tmp_path, monkeypatch):
    """get() should return the original entry when no overwrite happened."""
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    marks.save("x", 1.0, 2.0)
    m = marks.get("x")
    assert m["lat"] == 1.0
    assert m["lon"] == 2.0


# ── geocode ────────────────────────────────────────────────────────


@respx.mock
async def test_geocode():
    geocode.clear_cache()
    respx.get(url__startswith="https://nominatim.openstreetmap.org").mock(
        return_value=httpx.Response(200, json=[{"lat": "39.47", "lon": "75.99"}])
    )
    result = await geocode.lookup("喀什")
    # Offline GeoNames pack may return higher-precision coords;
    # accept result within ~1km of expected
    assert result is not None
    assert abs(result[0] - 39.47) < 0.01
    assert abs(result[1] - 75.99) < 0.01


@respx.mock
async def test_geocode_offline_fallback_when_down():
    """断网时离线 GeoNames 接管——断网也能去喀什。"""
    respx.route().mock(side_effect=httpx.TimeoutException("t"))
    result = await geocode.lookup("喀什")
    if geocode._PACK_PATH.exists():
        assert result is not None
        assert abs(result[0] - 39.47) < 0.5 and abs(result[1] - 75.99) < 0.5
    else:
        assert result is None


@respx.mock
async def test_geocode_none_when_down_and_no_pack(tmp_path, monkeypatch):
    """断网且所有离线源缺失 → None(降级尽头是承认不知道)。"""
    geocode.clear_cache()
    respx.route().mock(side_effect=httpx.TimeoutException("t"))
    monkeypatch.setattr(geocode, "_PACK_PATH", tmp_path / "nope.txt")
    monkeypatch.setattr("nowhere.places._DB", tmp_path / "nope.db")
    assert await geocode.lookup("喀什") is None
