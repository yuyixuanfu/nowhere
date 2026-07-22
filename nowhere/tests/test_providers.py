# nowhere/tests/test_providers.py
import httpx
from nowhere import providers

async def test_fetch_json_ok(respx_mock):
    respx_mock.get("https://a.com/x").mock(return_value=httpx.Response(200, json={"v": 1}))
    assert await providers.fetch_json("https://a.com/x", source="a") == {"v": 1}
    assert providers.provider_status()["a"] == "ok"

async def test_fetch_json_timeout_returns_none(respx_mock):
    respx_mock.get("https://b.com/x").mock(side_effect=httpx.TimeoutException("t"))
    assert await providers.fetch_json("https://b.com/x", source="b") is None

async def test_circuit_breaker_opens_after_3(respx_mock):
    respx_mock.get("https://c.com/x").mock(side_effect=httpx.TimeoutException("t"))
    for _ in range(3):
        await providers.fetch_json("https://c.com/x", source="c")
    assert providers.provider_status()["c"] == "down"
    respx_mock.get("https://c.com/x").mock(return_value=httpx.Response(200, json={"v": 2}))
    assert await providers.fetch_json("https://c.com/x", source="c") is None  # circuit open, no request

async def test_cache(respx_mock):
    route = respx_mock.get("https://d.com/x").mock(return_value=httpx.Response(200, json={"v": 1}))
    await providers.fetch_json("https://d.com/x", source="d", cache_ttl=60)
    await providers.fetch_json("https://d.com/x", source="d", cache_ttl=60)
    assert route.call_count == 1

async def test_degraded_state(respx_mock):
    respx_mock.get("https://e.com/x").mock(side_effect=httpx.TimeoutException("t"))
    await providers.fetch_json("https://e.com/x", source="e")
    assert providers.provider_status()["e"] == "degraded"


async def test_circuit_breaker_auto_reset(respx_mock, monkeypatch):
    """B11: after cooldown, circuit allows one probe request (half-open)."""
    # Use a fixed clock so we control timing precisely
    clock = [1000.0]
    monkeypatch.setattr(providers.time, "monotonic", lambda: clock[0])

    # 1. Fail 3 times → circuit opens (at clock=1000)
    respx_mock.get("https://f.com/x").mock(side_effect=httpx.TimeoutException("t"))
    for _ in range(3):
        await providers.fetch_json("https://f.com/x", source="f")
    assert providers.provider_status()["f"] == "down"

    # 2. Advance time past cooldown (60s)
    clock[0] = 1061.0

    # 3. Next call succeeds → status "ok"
    respx_mock.get("https://f.com/x").mock(return_value=httpx.Response(200, json={"v": 3}))
    result = await providers.fetch_json("https://f.com/x", source="f")
    assert result == {"v": 3}
    assert providers.provider_status()["f"] == "ok"
