"""Classificação central de falhas e cálculo de retry com jitter."""

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy.exc import DBAPIError, OperationalError


@dataclass(frozen=True)
class RetryDecision:
    retryable: bool
    delay_seconds: int = 0
    reason: str = "permanent"


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> int | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return max(0, int(value))
    try:
        target = parsedate_to_datetime(value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return max(0, int((target - current).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def exponential_backoff(
    attempt: int, *, base: int = 5, cap: int = 900, rng=random.uniform
) -> int:
    """Full jitter: reduz rajadas quando várias integrações falham juntas."""
    ceiling = min(cap, base * (2 ** max(0, attempt - 1)))
    return max(1, int(rng(0, ceiling)))


def classify_retry(exc: Exception, *, attempt: int) -> RetryDecision:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        retryable = status in {408, 409, 425, 429} or status >= 500
        if not retryable:
            return RetryDecision(False, reason=f"http_{status}")
        retry_after = parse_retry_after(exc.response.headers.get("retry-after"))
        delay = retry_after if retry_after is not None else exponential_backoff(attempt)
        return RetryDecision(True, min(3600, max(1, delay)), f"http_{status}")
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            OperationalError,
        ),
    ):
        return RetryDecision(True, exponential_backoff(attempt), "transient_io")
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return RetryDecision(True, exponential_backoff(attempt), "database_connection")
    return RetryDecision(False, reason=exc.__class__.__name__)
