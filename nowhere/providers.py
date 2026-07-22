"""Single HTTP exit point with timeout, circuit-breaker, and in-process cache."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Singleton client ──────────────────────────────────────────────
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": "nowhere-mcp/0.1"},
        )
    return _client


# ── Circuit-breaker state ─────────────────────────────────────────
# source -> consecutive failure count
_failure_counts: dict[str, int] = {}
# source -> monotonic timestamp when circuit opened
_circuit_opened_at: dict[str, float] = {}
CIRCUIT_OPEN_THRESHOLD = 3
CIRCUIT_COOLDOWN_SECONDS = 60.0


# ── Cache ─────────────────────────────────────────────────────────
# url -> (expiry_timestamp, data)
_cache: dict[str, tuple[float, Any]] = {}


# ── Public API ────────────────────────────────────────────────────

async def fetch_json(
    url: str,
    *,
    source: str,
    cache_ttl: float = 0,
    timeout: float = 2.0,
) -> dict | None:
    """Fetch JSON from *url*.  Never raises; returns ``None`` on any failure.

    Parameters
    ----------
    url:
        The URL to fetch.
    source:
        Circuit-breaker key (one key per upstream provider).
    cache_ttl:
        If > 0, cache the result for this many seconds.
    timeout:
        HTTP timeout in seconds.
    """
    # ── Circuit breaker ───────────────────────────────────────────
    if _failure_counts.get(source, 0) >= CIRCUIT_OPEN_THRESHOLD:
        opened_at = _circuit_opened_at.get(source, 0)
        if time.monotonic() - opened_at < CIRCUIT_COOLDOWN_SECONDS:
            return None
        # Cooldown elapsed → half-open: allow one probe request below

    # ── Cache lookup ──────────────────────────────────────────────
    if cache_ttl > 0 and url in _cache:
        expiry, data = _cache[url]
        if time.monotonic() < expiry:
            return data
        else:
            del _cache[url]

    # ── HTTP request ──────────────────────────────────────────────
    try:
        client = _get_client()
        resp = await client.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _failure_counts[source] = _failure_counts.get(source, 0) + 1
        if _failure_counts[source] >= CIRCUIT_OPEN_THRESHOLD:
            _circuit_opened_at[source] = time.monotonic()
            logger.warning("Circuit opened for %s after %d failures: %s", source, _failure_counts[source], exc)
        else:
            logger.debug("fetch_json %s failed (%d/%d): %s", source, _failure_counts[source], CIRCUIT_OPEN_THRESHOLD, exc)
        return None

    # ── Success: reset failure count, populate cache ──────────────
    _failure_counts[source] = 0

    if cache_ttl > 0:
        _cache[url] = (time.monotonic() + cache_ttl, data)

    return data


def provider_status() -> dict[str, str]:
    """Return ``{source: "ok" | "degraded" | "down"}`` for every known source."""
    result: dict[str, str] = {}
    all_sources = set(_failure_counts.keys())
    for src in all_sources:
        count = _failure_counts.get(src, 0)
        if count == 0:
            result[src] = "ok"
        elif count < CIRCUIT_OPEN_THRESHOLD:
            result[src] = "degraded"
        else:
            result[src] = "down"
    return result


def reset_for_tests() -> None:
    """Clear all circuit-breaker counts and the cache.  Called by conftest."""
    _failure_counts.clear()
    _circuit_opened_at.clear()
    _cache.clear()
