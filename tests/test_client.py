"""Tests for discogs_mcp.client: auth headers, pagination, retries, error mapping, rate limiting."""

from __future__ import annotations

import logging

import httpx
import pytest

from discogs_mcp import client as client_module
from discogs_mcp.client import USER_AGENT, DiscogsClient, DiscogsError, RateLimiter

from .conftest import TOKEN

pytestmark = pytest.mark.respx(base_url="https://api.discogs.com")


# -- auth / headers / logging -------------------------------------------------


async def test_sends_auth_token_and_user_agent(client, respx_mock):
    route = respx_mock.get("/oauth/identity").respond(json={"id": 1, "username": "tester"})
    await client.get("/oauth/identity")
    sent = route.calls[0].request.headers
    assert sent["Authorization"] == f"Discogs token={TOKEN}"
    assert sent["User-Agent"] == USER_AGENT
    assert sent["Accept"] == "application/vnd.discogs.v2.discogs+json"


async def test_token_never_appears_in_logs(client, respx_mock, caplog):
    respx_mock.get("/oauth/identity").respond(json={"id": 1, "username": "tester"})
    with caplog.at_level(logging.DEBUG):
        await client.get("/oauth/identity")
    for record in caplog.records:
        assert TOKEN not in record.getMessage()
        assert TOKEN not in str(record.args)


async def test_no_auth_header_without_token(respx_mock):
    c = DiscogsClient(None, backoff_base=0)
    route = respx_mock.get("/oauth/identity").respond(json={})
    await c.get("/oauth/identity")
    assert "Authorization" not in route.calls[0].request.headers
    assert c.has_token is False
    await c.aclose()


# -- pagination -----------------------------------------------------------


async def test_paginate_follows_next_url(client, respx_mock):
    page1 = {
        "items": [{"id": 1}, {"id": 2}],
        "pagination": {"urls": {"next": "https://api.discogs.com/things?page=2"}},
    }
    page2 = {"items": [{"id": 3}], "pagination": {"urls": {}}}
    route = respx_mock.get("/things").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )

    items = await client.collect("/things", "items")
    assert [i["id"] for i in items] == [1, 2, 3]
    assert route.call_count == 2
    # the second request hit the literal next URL, not a re-derived page param
    assert route.calls[1].request.url.params["page"] == "2"


async def test_paginate_respects_limit_and_stops_early(client, respx_mock):
    page1 = {
        "items": [{"id": 1}, {"id": 2}],
        "pagination": {"urls": {"next": "https://api.discogs.com/things?page=2"}},
    }
    page2 = {"items": [{"id": 3}], "pagination": {"urls": {}}}
    # A second page is mocked but must never be requested since limit=1 is hit on page 1.
    route = respx_mock.get("/things").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )

    items = await client.collect("/things", "items", limit=1)
    assert [i["id"] for i in items] == [1]
    assert route.call_count == 1


# -- retries / 429 ----------------------------------------------------------


async def test_429_retried_then_succeeds(client, respx_mock, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)
    route = respx_mock.get("/releases/1").mock(
        side_effect=[
            httpx.Response(429, headers={"X-Discogs-Ratelimit-Remaining": "0"}, json={"message": "rate limited"}),
            httpx.Response(200, json={"id": 1}),
        ]
    )
    data = await client.get("/releases/1")
    assert data == {"id": 1}
    assert route.call_count == 2
    assert sleeps  # a backoff sleep happened between attempts


async def test_429_exhausted_raises_rate_limit_error(client, respx_mock, monkeypatch):
    async def fake_sleep(d):
        return None

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)
    respx_mock.get("/releases/1").respond(429, json={"message": "Too Many Requests"})
    with pytest.raises(DiscogsError) as exc_info:
        await client.get("/releases/1")
    assert "rate limit" in str(exc_info.value).lower()
    assert exc_info.value.status == 429


async def test_network_error_retried_then_raises(client, respx_mock, monkeypatch):
    async def fake_sleep(d):
        return None

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)
    respx_mock.get("/releases/1").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(DiscogsError, match="Network error"):
        await client.get("/releases/1")


# -- error mapping ----------------------------------------------------------


async def test_401_error_message(client, respx_mock):
    respx_mock.get("/releases/1").respond(401, json={"message": "Invalid token"})
    with pytest.raises(DiscogsError) as exc_info:
        await client.get("/releases/1")
    assert exc_info.value.status == 401
    assert "DISCOGS_TOKEN" in str(exc_info.value)


async def test_404_error_message_includes_path(client, respx_mock):
    respx_mock.get("/releases/999999").respond(404, json={"message": "Release not found"})
    with pytest.raises(DiscogsError) as exc_info:
        await client.get("/releases/999999")
    assert exc_info.value.status == 404
    assert "/releases/999999" in str(exc_info.value)
    assert "Release not found" in str(exc_info.value)


async def test_403_price_suggestions_explains_seller_settings(client, respx_mock):
    respx_mock.get("/marketplace/price_suggestions/1").respond(403, json={"message": "Forbidden"})
    with pytest.raises(DiscogsError) as exc_info:
        await client.get("/marketplace/price_suggestions/1")
    assert exc_info.value.status == 403
    assert "seller settings" in str(exc_info.value)


async def test_403_generic_marketplace_message(client, respx_mock):
    respx_mock.get("/marketplace/listings/1").respond(403, json={"message": "no access"})
    with pytest.raises(DiscogsError) as exc_info:
        await client.get("/marketplace/listings/1")
    assert "no access" in str(exc_info.value)


async def test_403_other_resource_message(client, respx_mock):
    respx_mock.get("/users/someoneelse/collection/folders").respond(403, json={"message": "private"})
    with pytest.raises(DiscogsError) as exc_info:
        await client.get("/users/someoneelse/collection/folders")
    assert "private" in str(exc_info.value)


async def test_204_and_empty_body_return_empty_dict(client, respx_mock):
    respx_mock.delete("/users/tester/wants/1").respond(204)
    result = await client.request("DELETE", "/users/tester/wants/1")
    assert result == {}


# -- RateLimiter --------------------------------------------------------------


def test_rate_limiter_update_tracks_headers():
    limiter = RateLimiter()
    limiter.update(httpx.Headers({"X-Discogs-Ratelimit": "60", "X-Discogs-Ratelimit-Remaining": "3"}))
    assert limiter.limit == 60
    assert limiter.remaining == 3


def test_rate_limiter_update_ignores_bad_headers():
    limiter = RateLimiter()
    limiter.update(httpx.Headers({"X-Discogs-Ratelimit-Remaining": "not-a-number"}))
    assert limiter.remaining is None


def test_rate_limiter_delay_zero_when_remaining_high():
    limiter = RateLimiter(limit=60, low_water=5)
    limiter.remaining = 50
    assert limiter.delay() == 0.0


def test_rate_limiter_delay_zero_when_remaining_unknown():
    limiter = RateLimiter()
    assert limiter.remaining is None
    assert limiter.delay() == 0.0


def test_rate_limiter_delay_positive_when_remaining_low():
    limiter = RateLimiter(limit=60, low_water=5, window=60.0)
    limiter.remaining = 3
    assert limiter.delay() > 0


def test_rate_limiter_delay_highest_when_remaining_exhausted():
    limiter = RateLimiter(limit=60, low_water=5, window=60.0)
    limiter.remaining = 0
    exhausted_delay = limiter.delay()
    limiter.remaining = 3
    low_delay = limiter.delay()
    assert exhausted_delay > low_delay > 0


async def test_rate_limiter_acquire_sleeps_when_remaining_low(monkeypatch):
    limiter = RateLimiter(limit=60, low_water=5, window=60.0)
    limiter.remaining = 2
    limiter._last = 1000.0
    monkeypatch.setattr(client_module.time, "monotonic", lambda: 1000.0)
    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)
    await limiter.acquire()
    assert sleeps and sleeps[0] > 0


async def test_rate_limiter_acquire_does_not_sleep_when_remaining_high(monkeypatch):
    limiter = RateLimiter(limit=60, low_water=5, window=60.0)
    limiter.remaining = 50
    limiter._last = 1000.0
    monkeypatch.setattr(client_module.time, "monotonic", lambda: 1000.0)
    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)
    await limiter.acquire()
    assert sleeps == []


# -- page() helper ------------------------------------------------------------


async def test_page_returns_items_and_pagination_summary(client, respx_mock):
    respx_mock.get("/database/search").respond(
        json={
            "pagination": {"page": 2, "pages": 5, "items": 101, "per_page": 25},
            "results": [{"id": 1}],
        }
    )
    items, pagination = await client.page("/database/search", "results", page=2, per_page=25)
    assert items == [{"id": 1}]
    assert pagination == {"page": 2, "pages": 5, "items": 101, "per_page": 25}
