"""Access/Refresh tokens JWT com rotação e revogação (seção 10 do doc).

Arquitetura:
- Access Token: JWT de curta duração (ex.: 30 min) — autentica requisições.
- Refresh Token: JWT de longa duração (ex.: 7 dias) — renova o access.
- Rotação: cada uso do refresh gera um NOVO refresh e revoga o anterior.
- Revogação: sessões ativas no Redis; logout/logout-all invalidam.
- Proteção contra reutilização: se um refresh já rotacionado for
  reutilizado, a sessão inteira é revogada (mitigação de roubo).
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import redis.asyncio as aioredis

from app.core.config import get_settings

settings = get_settings()

_redis: aioredis.Redis | None = None

ACCESS_TYPE = "access"
REFRESH_TYPE = "refresh"

def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis

# ---------- geração ----------

def _create_token(
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    is_super_admin: bool,
    token_type: str,
    expires_delta: timedelta,
    jti: str | None = None,
    session_id: str | None = None,
) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    jti = jti or uuid4().hex
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "is_super_admin": is_super_admin,
        "jti": jti,
        "iat": now,
        "exp": now + expires_delta,
        "iss": settings.APP_NAME,
    }
    if session_id:
        payload["sid"] = session_id
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, jti

def create_access_token(
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    is_super_admin: bool,
) -> str:
    token, _ = _create_token(
        user_id=user_id,
        tenant_id=tenant_id,
        is_super_admin=is_super_admin,
        token_type=ACCESS_TYPE,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return token

def create_refresh_token(
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    is_super_admin: bool,
    session_id: str,
) -> tuple[str, str]:
    """Retorna (token, jti). O jti é usado para rotação/revogação."""
    return _create_token(
        user_id=user_id,
        tenant_id=tenant_id,
        is_super_admin=is_super_admin,
        token_type=REFRESH_TYPE,
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        session_id=session_id,
    )

# ---------- decodificação/validação ----------

class TokenError(Exception):
    """Falha de token (expirado, inválido, tipo errado)."""

def decode_token(token: str, expected_type: str) -> dict:
    """Decodifica e valida o JWT. Levanta TokenError em qualquer falha."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.APP_NAME,
        )
    except jwt.ExpiredSignatureError:
        raise TokenError("Token expirado.")
    except jwt.InvalidTokenError:
        raise TokenError("Token inválido.")
    if payload.get("type") != expected_type:
        raise TokenError("Tipo de token inválido.")
    return payload

# ---------- sessões (Redis) ----------

def _session_key(session_id: str) -> str:
    return f"auth:session:{session_id}"

def _user_sessions_key(user_id: UUID) -> str:
    return f"auth:sessions:{user_id}"

def _session_ttl() -> int:
    return settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400

async def create_session(
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    is_super_admin: bool,
) -> dict:
    """Cria uma sessão: gera refresh token e registra no Redis.

    Retorna dict com session_id, refresh_token e refresh_jti.
    """
    r = _get_redis()
    session_id = uuid4().hex
    refresh_token, refresh_jti = create_refresh_token(
        user_id=user_id,
        tenant_id=tenant_id,
        is_super_admin=is_super_admin,
        session_id=session_id,
    )
    ttl = _session_ttl()
    await r.hset(
        _session_key(session_id),
        mapping={
            "user_id": str(user_id),
            "tenant_id": str(tenant_id) if tenant_id else "",
            "is_super_admin": "1" if is_super_admin else "0",
            "refresh_jti": refresh_jti,
        },
    )
    await r.expire(_session_key(session_id), ttl)
    await r.sadd(_user_sessions_key(user_id), session_id)
    await r.expire(_user_sessions_key(user_id), ttl)
    return {
        "session_id": session_id,
        "refresh_token": refresh_token,
        "refresh_jti": refresh_jti,
    }

async def rotate_session(session_id: str, old_jti: str) -> tuple[str, str, dict]:
    """Rotaciona o refresh token.

    - Se o old_jti NÃO bate com o registrado → reutilização indevida:
      revoga a sessão inteira e levanta TokenError.
    - Se bate → gera novo refresh e atualiza o registro.
    """
    r = _get_redis()
    key = _session_key(session_id)
    stored = await r.hgetall(key)
    if not stored:
        raise TokenError("Sessão não encontrada.")
    if stored.get("refresh_jti") != old_jti:
        # Possível roubo/reutilização → derruba a sessão
        await revoke_session(session_id)
        raise TokenError("Sessão inválida.")
    new_token, new_jti = create_refresh_token(
        user_id=UUID(stored["user_id"]),
        tenant_id=UUID(stored["tenant_id"]) if stored.get("tenant_id") else None,
        is_super_admin=stored.get("is_super_admin") == "1",
        session_id=session_id,
    )
    await r.hset(key, "refresh_jti", new_jti)
    await r.expire(key, _session_ttl())
    return new_token, new_jti, stored

async def revoke_session(session_id: str) -> None:
    """Revoga uma sessão (logout ou detecção de reutilização)."""
    r = _get_redis()
    key = _session_key(session_id)
    user_id = await r.hget(key, "user_id")
    await r.delete(key)
    if user_id:
        await r.srem(_user_sessions_key(UUID(user_id)), session_id)

async def revoke_all_sessions(user_id: UUID) -> None:
    """Revoga todas as sessões do usuário (logout-all / troca de senha)."""
    r = _get_redis()
    key = _user_sessions_key(user_id)
    session_ids = await r.smembers(key)
    for sid in session_ids:
        await r.delete(_session_key(sid))
    await r.delete(key)