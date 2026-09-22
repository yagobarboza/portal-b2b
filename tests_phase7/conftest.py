from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy import delete

from app.database.session import async_session_factory, engine
from app.models import Company, ERPIntegration


@dataclass(frozen=True)
class IntegrationFixture:
    tenant_id: UUID
    integration_ids: dict[str, UUID]


@pytest_asyncio.fixture
async def integration_fixture() -> IntegrationFixture:
    """Tenant descartável real para as regressões que dependem de PostgreSQL."""
    suffix = uuid4().hex
    async with async_session_factory() as db:
        company = Company(
            name=f"Phase 7 {suffix[:8]}",
            cnpj=f"T7{suffix[:16]}",
            slug=f"phase-7-{suffix}",
        )
        db.add(company)
        await db.flush()
        integrations = {
            channel: ERPIntegration(
                tenant_id=company.id,
                name=f"{channel.title()} {suffix[:8]}",
                type=channel,
                is_active=True,
            )
            for channel in ("agent", "file", "webhook", "api")
        }
        db.add_all(integrations.values())
        await db.commit()
        fixture = IntegrationFixture(
            tenant_id=company.id,
            integration_ids={key: value.id for key, value in integrations.items()},
        )

    try:
        yield fixture
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(Company).where(Company.id == fixture.tenant_id))
            await db.commit()
        # O projeto usa loop por teste; não deixe conexões asyncpg presas ao loop
        # que será fechado pelo pytest-asyncio.
        await engine.dispose()
