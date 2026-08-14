"""Configuração de cookies seguros (seção 10 do doc)."""
from fastapi import Response

from app.core.config import get_settings

settings = get_settings()

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"

def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Grava access/refresh em cookies HttpOnly seguros."""
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=settings.COOKIE_HTTPONLY,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        httponly=settings.COOKIE_HTTPONLY,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        path="/",
    )

def clear_auth_cookies(response: Response) -> None:
    """Remove os cookies de autenticação (logout)."""
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")