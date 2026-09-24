from __future__ import annotations

import logging

import pytest
from alembic.config import Config
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import SENSITIVE_DEPENDENCY_LOGGERS, setup_logging
from app.core.redis_settings import arq_redis_settings, redis_client_kwargs
from app.database.alembic_url import escape_alembic_url
from worker.runtime import metrics_port


def production_settings(**overrides) -> Settings:
    values = {
        "APP_ENV": "production",
        "ENVIRONMENT": "production",
        "SECRET_KEY": "a" * 64,
        "INTEGRATION_ENCRYPTION_KEY": "b" * 64,
        "COOKIE_SECURE": True,
        "CORS_ORIGINS": "https://portal.example.com",
        "FRONTEND_BASE_URL": "https://portal.example.com",
        "API_SCHEDULER_ENABLED": False,
        "DATABASE_URL": "postgresql+asyncpg://portal:secret@db/portal",
        "REDIS_URL": "redis://redis:6379/0",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_configuration_accepts_safe_runtime() -> None:
    settings = production_settings()
    assert settings.COOKIE_SECURE is True
    assert settings.API_SCHEDULER_ENABLED is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("API_SCHEDULER_ENABLED", True),
        ("COOKIE_SECURE", False),
        ("COOKIE_HTTPONLY", False),
        ("APP_DEBUG", True),
        ("ENVIRONMENT", "development"),
        ("CORS_ORIGINS", "*"),
        ("CORS_ORIGINS", "http://localhost:5173"),
        ("CORS_ORIGINS", "http://portal.example.com"),
        ("FRONTEND_BASE_URL", "http://portal.example.com"),
        ("DATABASE_URL", ""),
        ("REDIS_URL", ""),
    ],
)
def test_production_configuration_rejects_unsafe_values(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        production_settings(**{field: value})


def test_redis_tls_ca_is_shared_by_api_and_arq() -> None:
    settings = production_settings(
        REDIS_URL="rediss://user:secret@redis.example.com:6380/1",
        REDIS_SSL_CA_CERTS="/var/run/secrets/redis/ca.pem",
    )
    client = redis_client_kwargs(settings)
    worker = arq_redis_settings(settings)

    assert client["ssl_cert_reqs"] == "required"
    assert client["ssl_ca_certs"] == "/var/run/secrets/redis/ca.pem"
    assert worker.ssl is True
    assert worker.ssl_ca_certs == "/var/run/secrets/redis/ca.pem"
    assert worker.database == 1


def test_worker_prefers_cloud_run_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "8080")
    assert metrics_port() == 8080


def test_alembic_accepts_percent_encoded_database_password() -> None:
    database_url = (
        "postgresql+asyncpg://portal_app:NydB2%40secret@10.30.0.3:5432/portal_b2b"
    )
    config = Config()
    config.set_main_option("sqlalchemy.url", escape_alembic_url(database_url))

    assert config.get_main_option("sqlalchemy.url") == database_url


def test_sensitive_sdk_debug_logs_are_suppressed() -> None:
    setup_logging()

    for logger_name in SENSITIVE_DEPENDENCY_LOGGERS:
        assert logging.getLogger(logger_name).getEffectiveLevel() >= logging.WARNING
