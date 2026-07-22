"""Capture and analyse a live radio stream.

* With ffmpeg: decode 10 s of audio to PCM, run numpy spectral analysis.
* Without ffmpeg (degraded): read raw bytes, estimate jitter/energy, infer
  texture from genre metadata. ``analyzed=False`` signals the degraded path.
* Never raises.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from typing import Final

import numpy as np

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

# ── Constants ───────────────────────────────────────────────────────

_FFT_SIZE: Final = 2048
_HOP_SIZE: Final = 512
_VOICE_LOW: Final = 300.0
_VOICE_HIGH: Final = 3400.0


# ── Spectral helpers ────────────────────────────────────────────────

def _stft_frames(samples: np.ndarray, fft_size: int, hop_size: int) -> np.ndarray:
    """Return magnitude spectrogram frames (rows = time)."""
    n_frames = max(1, (len(samples) - fft_size) // hop_size + 1)
    window = np.hanning(fft_size)
    frames = np.empty((n_frames, fft_size // 2 + 1))
    for i in range(n_frames):
        start = i * hop_size
        chunk = samples[start : start + fft_size] * window
        spectrum = np.abs(np.fft.rfft(chunk))
        frames[i] = spectrum
    return frames


def _spectral_flux(frames: np.ndarray) -> np.ndarray:
    """Half-wave rectified spectral flux (onset detection envelope)."""
    diff = np.diff(frames, axis=0)
    flux = np.sum(np.maximum(diff, 0), axis=1)
    return flux


# ── Public: analyse PCM ────────────────────────────────────────────

def analyze_pcm(samples: np.ndarray, sr: int) -> dict:
    """Analyze a 1-D float32 PCM signal at sample rate *sr*.

    Returns keys: tempo_density, rms, centroid_hz, has_voice, texture, analyzed.
    """
    if len(samples) == 0:
        return {
            "tempo_density": 0.0,
            "rms": 0.0,
            "centroid_hz": 0.0,
            "has_voice": False,
            "texture": "sparse",
            "analyzed": True,
        }

    samples = samples.astype(np.float32)

    # RMS
    rms = float(np.sqrt(np.mean(samples**2)))
    rms = min(rms, 1.0)

    # STFT
    frames = _stft_frames(samples, _FFT_SIZE, _HOP_SIZE)
    n_frames = len(frames)
    duration = len(samples) / sr

    # Spectral centroid
    freqs = np.fft.rfftfreq(_FFT_SIZE, d=1.0 / sr)
    mag_sum = frames.sum(axis=0)
    total_mag = mag_sum.sum()
    if total_mag > 0:
        centroid_hz = float(np.sum(freqs * mag_sum) / total_mag)
    else:
        centroid_hz = 0.0

    # Onset density (spectral flux peaks)
    flux = _spectral_flux(frames)
    if len(flux) > 2:
        threshold = np.mean(flux) + 0.5 * np.std(flux)
        peaks = np.where(flux > threshold)[0]
        tempo_density = float(len(peaks) / duration) if duration > 0 else 0.0
    else:
        tempo_density = 0.0

    # Voice detection: energy in 300-3400 Hz band vs total
    band_mask = (freqs >= _VOICE_LOW) & (freqs <= _VOICE_HIGH)
    band_energy = mag_sum[band_mask].sum()
    has_voice = bool(band_energy / total_mag > 0.5) if total_mag > 0 else False

    # Texture classification
    texture = classify_texture(tempo_density, centroid_hz)

    return {
        "tempo_density": round(tempo_density, 3),
        "rms": round(rms, 4),
        "centroid_hz": round(centroid_hz, 1),
        "has_voice": has_voice,
        "texture": texture,
        "analyzed": True,
    }


# ── Texture classifier ─────────────────────────────────────────────

def classify_texture(tempo_density: float, centroid_hz: float) -> str:
    """Classify audio texture from onset density and spectral centroid.

    Returns one of: ``"sparse"``, ``"smooth"``, ``"dense"``, ``"harsh"``.

    Decision grid::

                low centroid   high centroid (>3000 Hz)
        low td    sparse        smooth
        high td   dense         harsh

    """
    HIGH_CENTROID = 4500.0  # Hz
    HIGH_DENSITY = 4.0      # onsets/s

    high_freq = centroid_hz >= HIGH_CENTROID
    high_rate = tempo_density >= HIGH_DENSITY

    if high_freq and high_rate:
        return "harsh"
    if high_rate:
        return "dense"
    if tempo_density < 1.0:
        return "sparse"
    return "smooth"


# ── Degraded analysis (no ffmpeg) ──────────────────────────────────

def _degraded_result(genre: str = "") -> dict:
    """Produce a best-guess result when we cannot decode audio."""
    genre_lower = genre.lower() if genre else ""

    # Heuristic texture from genre keywords
    if any(k in genre_lower for k in ("ambient", "drone", "classical", "new age")):
        texture = "sparse"
    elif any(k in genre_lower for k in ("rock", "metal", "punk", "industrial")):
        texture = "harsh"
    elif any(k in genre_lower for k in ("jazz", "soul", "folk", "acoustic")):
        texture = "smooth"
    else:
        texture = "dense"

    return {
        "tempo_density": 0.0,
        "rms": 0.0,
        "centroid_hz": 0.0,
        "has_voice": False,
        "texture": texture,
        "analyzed": False,
    }


# ── Public: capture stream ─────────────────────────────────────────

async def capture(stream_url: str, seconds: int = 10) -> dict:
    """Capture *seconds* of audio from *stream_url* and analyze it.

    Uses ffmpeg when available; otherwise falls back to a degraded byte-count
    heuristic.  **Never raises** — on any failure returns ``analyzed=False``.
    """
    try:
        return await _capture_ffmpeg(stream_url, seconds)
    except Exception:
        pass

    try:
        return await _capture_degraded(stream_url, seconds)
    except Exception:
        return _degraded_result()


async def _capture_ffmpeg(stream_url: str, seconds: int) -> dict:
    """Decode via ffmpeg subprocess → numpy → analyze_pcm."""
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError("ffmpeg not on PATH")

    cmd = [
        "ffmpeg",
        "-i", stream_url,
        "-t", str(seconds),
        "-f", "wav",
        "-acodec", "pcm_s16le",
        "-ar", "22050",
        "-ac", "1",
        "-loglevel", "error",
        "pipe:1",
    ]

    proc = await _run_subprocess(cmd, timeout=seconds + 15)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr}")

    # Parse WAV: skip 44-byte RIFF header
    raw = proc.stdout
    if len(raw) < 44:
        raise ValueError("WAV too short")

    pcm = np.frombuffer(raw[44:], dtype=np.int16).astype(np.float32) / 32768.0
    if len(pcm) == 0:
        raise ValueError("Empty PCM")

    return analyze_pcm(pcm, 22050)


async def _run_subprocess(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run a subprocess asynchronously (platform-safe)."""
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        ),
    )


async def _capture_degraded(stream_url: str, seconds: int) -> dict:
    """Read raw stream bytes for *seconds* and estimate rough metrics."""
    if httpx is None:
        return _degraded_result()

    byte_counts: list[int] = []
    total_bytes = 0
    start = time.monotonic()

    async with httpx.AsyncClient(timeout=float(seconds + 5)) as client:
        async with client.stream("GET", stream_url) as resp:
            resp.raise_for_status()
            chunk_start = time.monotonic()
            chunk_bytes = 0
            async for chunk in resp.aiter_bytes(4096):
                chunk_bytes += len(chunk)
                total_bytes += len(chunk)
                elapsed = time.monotonic() - chunk_start
                if elapsed >= 1.0:
                    byte_counts.append(chunk_bytes)
                    chunk_bytes = 0
                    chunk_start = time.monotonic()
                if time.monotonic() - start >= seconds:
                    break
            if chunk_bytes > 0:
                byte_counts.append(chunk_bytes)

    if total_bytes == 0:
        return _degraded_result()

    # Byte-rate jitter as proxy for onset density
    if len(byte_counts) >= 2:
        rates = np.array(byte_counts, dtype=np.float32)
        jitter = float(np.std(rates) / (np.mean(rates) + 1e-6))
    else:
        jitter = 0.0

    # Map jitter → rough tempo_density (heuristic)
    tempo_density = jitter * 5.0
    centroid_hz = 2000.0  # unknown, assume mid
    texture = classify_texture(tempo_density, centroid_hz)

    return {
        "tempo_density": round(tempo_density, 3),
        "rms": 0.0,
        "centroid_hz": centroid_hz,
        "has_voice": False,
        "texture": texture,
        "analyzed": False,
    }
