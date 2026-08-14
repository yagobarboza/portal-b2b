"""Rate limiting centralizado (seções 8 e 39 do doc).

Usa slowapi com storage Redis: o limite é compartilhado entre
todas as instâncias da API (aplicação stateless, escalável).
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.redis_url,
    storage_options={"socket_connect_timeout": 2},
    headers_enabled=True,
    default_limits=[],  # limites são definidos por endpoint
)