from __future__ import annotations

import asyncio

import pytest

from app.api.v1.endpoints import company as company_endpoint
from app.core.exceptions import RateLimitedError


def test_domain_rate_limit_allows_when_counter_is_not_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def not_blocked(key: str, limit: int, window: int) -> bool:
        assert key == "domain:portal.example.com"
        assert limit == 30
        assert window == 60
        return False

    monkeypatch.setattr(company_endpoint, "check_rate_limit", not_blocked)

    asyncio.run(company_endpoint._check_domain_rate_limit("Portal.Example.com"))


def test_domain_rate_limit_rejects_when_counter_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def blocked(key: str, limit: int, window: int) -> bool:
        return True

    monkeypatch.setattr(company_endpoint, "check_rate_limit", blocked)

    with pytest.raises(RateLimitedError):
        asyncio.run(company_endpoint._check_domain_rate_limit("portal.example.com"))
