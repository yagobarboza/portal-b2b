"""Hardening HTTP (Bloco 13 — seção 67).

- Headers de segurança em toda resposta.
- CORS restrito por allowlist (CORS_ORIGINS do settings).

Separado de app/core/security.py (que cuida de hashing de senha) para
não conflitar responsabilidades.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adiciona headers de segurança em todas as respostas."""

    async def dispatch(self, request, call_next):  # noqa: ANN001
        response = await call_next(request)
        settings = get_settings()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        if settings.ENVIRONMENT == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

def add_security_middleware(app: FastAPI) -> None:
    app.add_middleware(SecurityHeadersMiddleware)

def add_cors(app: FastAPI) -> None:
    """CORS restrito: só origens da allowlist (seção 67)."""
    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["*"],
    )