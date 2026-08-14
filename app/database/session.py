"""Sessão de banco (Bloco 14 — seção 53/54).

- Pool de conexões configurado para escalar com múltiplas réplicas da API.
- pool_pre_ping: evita usar conexões mortas.
- pool_recycle: renova conexões antes de expirar (NAT/long-lived).
- max_overflow: teto de conexões extras para não estourar o Postgres.
"""
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

# Tuning de pool (seção 54): valores ajustáveis via .env com defaults seguros.
POOL_SIZE = int(getattr(settings, "DB_POOL_SIZE", 10))
MAX_OVERFLOW = int(getattr(settings, "DB_MAX_OVERFLOW", 20))
POOL_RECYCLE = int(getattr(settings, "DB_POOL_RECYCLE", 1800))  # 30 min

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,          # evita usar conexões mortas
    pool_size=POOL_SIZE,         # conexões mantidas abertas
    max_overflow=MAX_OVERFLOW,   # teto de conexões extras sob pico
    pool_recycle=POOL_RECYCLE,   # renova antes de expirar (NAT)
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency do FastAPI: fornece uma sessão por requisição."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise