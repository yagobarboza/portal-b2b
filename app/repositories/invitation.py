"""Repositório de convites."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.invitations import hash_invite_token
from app.models.invitation import Invitation, InvitationStatus

class InvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        email: str,
        role_slug: str,
        token: str,
        expires_at: datetime,
        tenant_id: UUID | None = None,
        full_name: str | None = None,
        invited_by: UUID | None = None,
        customer_id: UUID | None = None,  # NOVO: vínculo ao cliente
    ) -> Invitation:
        invitation = Invitation(
            tenant_id=tenant_id,
            customer_id=customer_id,
            email=email,
            full_name=full_name,
            role_slug=role_slug,
            token_hash=hash_invite_token(token),
            status=InvitationStatus.PENDING,
            expires_at=expires_at,
            invited_by=invited_by,
        )
        self.session.add(invitation)
        await self.session.flush()
        return invitation

    async def get_by_token(self, token: str) -> Invitation | None:
        stmt = select(Invitation).where(
            Invitation.token_hash == hash_invite_token(token)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get(self, invitation_id: UUID) -> Invitation:
        invitation = await self.session.get(Invitation, invitation_id)
        if invitation is None:
            raise NotFoundError("Convite não encontrado.")
        return invitation

    async def list_by_tenant(self, tenant_id: UUID | None) -> list[Invitation]:
        stmt = select(Invitation).order_by(Invitation.created_at.desc())
        if tenant_id is not None:
            stmt = stmt.where(Invitation.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_accepted(self, invitation: Invitation) -> None:
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def cancel(self, invitation: Invitation) -> None:
        invitation.status = InvitationStatus.CANCELLED
        await self.session.flush()