"""Configuracao Redis compartilhada pela API, fila e workers."""

from typing import Any

from arq.connections import RedisSettings

from app.core.config import Settings, get_settings


def redis_client_kwargs(settings: Settings | None = None) -> dict[str, Any]:
    """Parametros seguros para ``redis-py``.

    A CA so e enviada para URLs ``rediss://``; passar parametros SSL para
    ``redis://`` faz o redis-py tentar usa-los em uma conexao TCP comum.
    """
    cfg = settings or get_settings()
    kwargs: dict[str, Any] = {
        "encoding": "utf-8",
        "decode_responses": True,
        "max_connections": cfg.REDIS_MAX_CONNECTIONS,
        "socket_connect_timeout": 3,
        "socket_timeout": 5,
        "health_check_interval": 30,
    }
    if cfg.redis_url.lower().startswith("rediss://"):
        kwargs["ssl_cert_reqs"] = "required"
        kwargs["ssl_check_hostname"] = cfg.REDIS_SSL_CHECK_HOSTNAME
        if cfg.REDIS_SSL_CA_CERTS:
            kwargs["ssl_ca_certs"] = cfg.REDIS_SSL_CA_CERTS
    return kwargs


def arq_redis_settings(settings: Settings | None = None) -> RedisSettings:
    """Cria settings ARQ equivalentes aos usados pelo cliente da API."""
    cfg = settings or get_settings()
    result = RedisSettings.from_dsn(cfg.redis_url)
    result.max_connections = cfg.REDIS_MAX_CONNECTIONS
    result.conn_timeout = 3
    result.ssl_check_hostname = cfg.REDIS_SSL_CHECK_HOSTNAME
    if result.ssl and cfg.REDIS_SSL_CA_CERTS:
        result.ssl_ca_certs = cfg.REDIS_SSL_CA_CERTS
    return result
