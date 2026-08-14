"""Middleware de contexto de requisição.

- Gera um request_id único por requisição (rastreabilidade, seção 43).
- Injeta o request_id no structlog contextvars (aparece em todos os logs).
- Limpa o TenantContext ao final de cada requisição (evita vazamento
  de contexto entre requisições — seção 5).
"""
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

import structlog

from app.core.context import TenantContext

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = uuid.uuid4().hex

        # Injeta o request_id no contexto de logging
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Configura o contexto de tenant para esta requisição
        TenantContext.set(
            tenant_id=None,
            user_id=None,
            request_id=request_id,
            is_super_admin=False,
        )

        response = await call_next(request)

        # Limpa o contexto ao final (nunca vaza para a próxima requisição)
        TenantContext.reset()
        structlog.contextvars.clear_contextvars()

        response.headers["X-Request-ID"] = request_id
        return response