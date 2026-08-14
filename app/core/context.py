from contextvars import ContextVar
from uuid import UUID

_tenant_id: ContextVar[UUID | None] = ContextVar("tenant_id", default=None)
_user_id: ContextVar[UUID | None] = ContextVar("user_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_is_super_admin: ContextVar[bool] = ContextVar("is_super_admin", default=False)

class TenantContext:
    """Contexto de execução da requisição corrente.

    É a base do isolamento multi-tenant: toda query consulta
    o tenant_id armazenado aqui (seções 5 e 14 do documento).
    """

    @staticmethod
    def set(
        tenant_id: UUID | None,
        user_id: UUID | None = None,
        request_id: str | None = None,
        is_super_admin: bool = False,
    ) -> None:
        _tenant_id.set(tenant_id)
        _user_id.set(user_id)
        _request_id.set(request_id)
        _is_super_admin.set(is_super_admin)

    @staticmethod
    def tenant_id() -> UUID | None:
        return _tenant_id.get()

    @staticmethod
    def user_id() -> UUID | None:
        return _user_id.get()

    @staticmethod
    def request_id() -> str | None:
        return _request_id.get()

    @staticmethod
    def is_super_admin() -> bool:
        return _is_super_admin.get()

    @staticmethod
    def reset() -> None:
        _tenant_id.set(None)
        _user_id.set(None)
        _request_id.set(None)
        _is_super_admin.set(False)