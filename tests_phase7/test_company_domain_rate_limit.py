from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.api.v1.endpoints import company as company_endpoint
from app.core.exceptions import RateLimitedError
from app.repositories.company import CompanyRepository
from app.schemas.invitation import CompanyCreateRequest


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


def test_company_repository_normalizes_tenant_identifiers() -> None:
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    repo = CompanyRepository(db)

    company = asyncio.run(
        repo.create_with_tenant(
            name=" Empresa Exemplo ",
            cnpj="00.000.000/0001-00",
            slug="EMPRESA-EXEMPLO",
            domain=" Portal.Example.com ",
            primary_color="#112233",
            secondary_color="#445566",
            logo_url=None,
            favicon_url=None,
        )
    )

    assert company.name == "Empresa Exemplo"
    assert company.slug == "empresa-exemplo"
    assert company.domain == "portal.example.com"
    db.add.assert_called_once_with(company)
    db.flush.assert_awaited_once()


def test_create_company_persists_invitation_without_premature_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = uuid4()
    role_id = uuid4()
    invitation_id = uuid4()
    admin_id = uuid4()
    company = SimpleNamespace(
        id=company_id,
        name="Empresa Exemplo",
        slug="empresa-exemplo",
        domain="portal.example.com",
    )
    role = SimpleNamespace(id=role_id, slug="admin")
    invitation = SimpleNamespace(id=invitation_id)
    queued: dict[str, object] = {}

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [role]

    class FakeDB:
        def __init__(self) -> None:
            self.execute_count = 0
            self.commit = AsyncMock()

        async def execute(self, statement, parameters=None):
            self.execute_count += 1
            return Result()

    class FakeCompanyRepository:
        def __init__(self, db) -> None:
            pass

        async def find_by_slug_or_domain_or_cnpj(self, slug, domain, cnpj):
            return None

        async def create_with_tenant(self, **kwargs):
            return company

    class FakeUserRepository:
        def __init__(self, db) -> None:
            pass

        async def get_by_email(self, email):
            return None

    class FakeInvitationRepository:
        def __init__(self, db) -> None:
            pass

        async def create(self, **kwargs):
            assert kwargs["tenant_id"] == company_id
            assert kwargs["role_slug"] == "admin"
            assert kwargs["invited_by"] == admin_id
            return invitation

    async def fake_enqueue(function: str, **kwargs) -> bool:
        queued["function"] = function
        queued.update(kwargs)
        return True

    monkeypatch.setattr(company_endpoint, "CompanyRepository", FakeCompanyRepository)
    monkeypatch.setattr(company_endpoint, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(
        company_endpoint, "InvitationRepository", FakeInvitationRepository
    )
    monkeypatch.setattr(
        company_endpoint,
        "ROLE_DEFINITIONS",
        {"admin": {"name": "Admin", "permissions": []}},
    )
    monkeypatch.setattr(company_endpoint, "generate_invite_token", lambda: "token")
    monkeypatch.setattr(
        company_endpoint,
        "compute_expires_at",
        lambda: datetime.now(timezone.utc) + timedelta(hours=72),
    )
    monkeypatch.setattr(company_endpoint, "enqueue_job", fake_enqueue)
    monkeypatch.setattr(company_endpoint, "record_audit", AsyncMock())
    monkeypatch.setattr(
        company_endpoint,
        "get_settings",
        lambda: SimpleNamespace(
            FRONTEND_BASE_URL="https://app.nydsoftwares.com.br",
            INVITE_TOKEN_EXPIRE_HOURS=72,
        ),
    )

    response = asyncio.run(
        company_endpoint.create_company_with_admin(
            CompanyCreateRequest(
                name="Empresa Exemplo",
                cnpj="00000000000100",
                slug="empresa-exemplo",
                domain="portal.example.com",
                admin_email="admin@example.com",
                admin_full_name="Admin Exemplo",
            ),
            db=FakeDB(),
            user=SimpleNamespace(id=admin_id),
        )
    )

    assert response["id"] == company_id
    assert response["invitation_id"] == invitation_id
    assert response["invite_queued"] is True
    assert queued["function"] == "send_invite_email_job"
    assert queued["invite_url"] == (
        "https://portal.example.com/accept-invite?token=token"
    )
