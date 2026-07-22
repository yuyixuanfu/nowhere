"""Tests for listen.py and radio.py — Task 06."""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from nowhere import listen, radio


# ── radio.py tests ──────────────────────────────────────────────────

class TestRadioNearest:
    """radio.nearest with live API mocked away."""

    @pytest.mark.asyncio
    async def test_radio_fallback_when_all_down(self, respx_mock):
        """When every mirror times out, fallback JSON should still produce a station."""
        respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
        r = await radio.nearest(35.6, 139.7, "JP")
        assert r is not None
        assert r["stream_url"].startswith("http")
        assert "name" in r

    @pytest.mark.asyncio
    async def test_radio_returns_dict_keys(self, respx_mock):
        """Fallback result must have all required keys."""
        respx_mock.route().mock(side_effect=httpx.ConnectTimeout("no"))
        r = await radio.nearest(51.5, -0.1, "GB")
        assert r is not None
        for key in ("name", "genre", "stream_url", "homepage"):
            assert key in r

    @pytest.mark.asyncio
    async def test_radio_nearest_no_country(self, respx_mock):
        """When country_code is None, should still fall back to lat/lon pick."""
        respx_mock.route().mock(side_effect=httpx.TimeoutException("t"))
        r = await radio.nearest(48.8, 2.3, None)
        assert r is not None
        assert r["stream_url"].startswith("http")


# ── listen.py: analyze_pcm ──────────────────────────────────────────

class TestAnalyzePcm:
    def test_analyze_sine_wav(self, tmp_path, monkeypatch):
        """A 440 Hz sine wave should have centroid ~440 Hz and decent RMS."""
        sr = 22050
        t = np.arange(sr * 3) / sr
        sig = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
        r = listen.analyze_pcm(sig, sr)
        assert r["analyzed"] is True
        assert 300 < r["centroid_hz"] < 2000
        assert r["rms"] > 0.2

    def test_silence(self):
        """Silence should yield near-zero RMS and analyzed=True."""
        sr = 22050
        sig = np.zeros(sr * 2, dtype=np.float32)
        r = listen.analyze_pcm(sig, sr)
        assert r["analyzed"] is True
        assert r["rms"] < 0.01

    def test_white_noise_has_voice_false(self):
        """White noise has energy spread across all bands; voice ratio should be < 0.5."""
        rng = np.random.default_rng(42)
        sr = 22050
        sig = (rng.standard_normal(sr * 3) * 0.3).astype(np.float32)
        r = listen.analyze_pcm(sig, sr)
        assert r["analyzed"] is True
        # White noise: 300-3400Hz / total should be ~0.54 depending on FFT,
        # but we at least check it returns a bool
        assert isinstance(r["has_voice"], bool)
        assert r["has_voice"] is False, "White noise should not be classified as voice"


# ── listen.py: classify_texture ─────────────────────────────────────

class TestClassifyTexture:
    def test_texture_classification(self):
        from nowhere.listen import classify_texture

        assert classify_texture(tempo_density=6.0, centroid_hz=4000) == "dense"
        assert classify_texture(tempo_density=0.5, centroid_hz=800) == "sparse"
        assert classify_texture(tempo_density=3.0, centroid_hz=1500) == "smooth"
        assert classify_texture(tempo_density=5.0, centroid_hz=5000) == "harsh"

    def test_sparse_low_energy(self):
        from nowhere.listen import classify_texture

        assert classify_texture(tempo_density=0.2, centroid_hz=500) == "sparse"


# ── listen.py: capture (degraded path, no ffmpeg) ───────────────────

class TestCapture:
    @pytest.mark.asyncio
    async def test_capture_never_raises(self, monkeypatch):
        """capture must never raise; should return analyzed=False on failure."""
        # Monkeypatch ffmpeg as unavailable
        monkeypatch.setattr("shutil.which", lambda _: None)
        # Monkeypatch httpx stream to raise immediately
        import httpx as _httpx

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def stream(self, method, url, **kw):
                raise _httpx.ConnectError("no network")

        monkeypatch.setattr(_httpx, "AsyncClient", lambda **kw: _FakeClient())

        r = await listen.capture("http://fake.stream/test.mp3", seconds=10)
        assert isinstance(r, dict)
        assert r["analyzed"] is False
        assert "texture" in r

    @pytest.mark.asyncio
    async def test_capture_returns_all_keys(self, monkeypatch):
        """Result must have every key from the contract."""
        monkeypatch.setattr("shutil.which", lambda _: None)

        import httpx as _httpx

        # Simulate a slow byte stream
        async def _fake_iter_bytes(*a, **kw):
            yield b"\x00" * 100

        class _FakeResponse:
            status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def aiter_bytes(self, *a, **kw):
                for _ in range(5):
                    yield b"\x80" * 200

            def raise_for_status(self):
                pass

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def stream(self, method, url, **kw):
                return _FakeResponse()

        monkeypatch.setattr(_httpx, "AsyncClient", lambda **kw: _FakeClient())

        r = await listen.capture("http://fake.stream/test.mp3", seconds=2)
        for key in ("tempo_density", "rms", "centroid_hz", "has_voice", "texture", "analyzed"):
            assert key in r, f"missing key: {key}"
