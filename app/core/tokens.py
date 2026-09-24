"""Access/Refresh tokens JWT com rotação e revogação (seção 10 do doc).

Arquitetura:
- Access Token: JWT de curta duração (ex.: 30 min) — autentica requisições.
- Refresh Token: JWT de longa duração (ex.: 7 dias) — renova o access.
- Rotação: cada uso do refresh gera um NOVO refresh e revoga o anterior.
- Revogação: sessões ativas no Redis; logout/logout-all invalidam.
- Proteção contra reutilização: se um refresh já rotacionado for
  reutilizado, a sessão inteira é revogada (mitigação de roubo).
- MFA (seção 11): token de DESAFIO de curta duração (uso único) emitido
  no login quando o usuário tem 2FA ativo. Só após validar o código é que
  a sessão (access + refresh) é criada.
- Blacklist do access token (logout): o access é um JWT stateless válido
  por ~30 min. Sem blacklist, ele continua autenticando mesmo após o
  logout. Ao deslogar, o `jti` do access é gravado no Redis com TTL até
  expirar; qualquer endpoint que valida o access (ex.: /auth/me) rejeita
  tokens na blacklist.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.redis_settings import create_redis_client

settings = get_settings()

_redis: Redis | None = None

ACCESS_TYPE = "access"
REFRESH_TYPE = "refresh"
# ✅ Tipo do token de desafio MFA (2º fator pendente no login).
MFA_TYPE = "mfa_challenge"

def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = create_redis_client(settings)
    return _redis

def _mfa_challenge_ttl() -> int:
    """TTL do desafio MFA em segundos (padrão: 5 min)."""
    return int(getattr(settings, "MFA_CHALLENGE_EXPIRE_SECONDS", 300))

def _mfa_challenge_key(jti: str) -> str:
    return f"auth:mfa_challenge:{jti}"

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

# ---------- desafio MFA (2º fator no login) ----------

async def create_mfa_challenge_token(
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    is_super_admin: bool,
) -> str:
    """Gera o token de desafio MFA (curta duração, uso único).

    O `jti` é registrado no Redis com TTL curto. A sessão REAL (access +
    refresh) só é criada depois que o código TOTP for validado.
    """
    token, jti = _create_token(
        user_id=user_id,
        tenant_id=tenant_id,
        is_super_admin=is_super_admin,
        token_type=MFA_TYPE,
        expires_delta=timedelta(seconds=_mfa_challenge_ttl()),
    )
    r = _get_redis()
    await r.set(_mfa_challenge_key(jti), str(user_id), ex=_mfa_challenge_ttl())
    return token

async def validate_mfa_challenge(token: str) -> dict:
    """Valida o desafio MFA SEM consumi-lo (permite nova tentativa do código).

    Proteção: o desafio tem TTL curto e o endpoint de verificação é
    rate-limited por IP. Só é consumido (revogado) quando o código acerta.
    Levanta TokenError se inválido, expirado ou não registrado no Redis.
    """
    payload = decode_token(token, MFA_TYPE)
    jti = payload.get("jti")
    if not jti:
        raise TokenError("Token inválido.")
    r = _get_redis()
    stored = await r.get(_mfa_challenge_key(jti))
    if stored is None:
        raise TokenError("Desafio expirado.")
    return payload

async def revoke_mfa_challenge(payload: dict) -> None:
    """Consome o desafio MFA (uso único) após o segundo fator ser validado."""
    jti = payload.get("jti")
    if not jti:
        return
    r = _get_redis()
    await r.delete(_mfa_challenge_key(jti))

# ---------- blacklist do access token (logout) ----------

def _access_blacklist_key(jti: str) -> str:
    return f"auth:access_blacklist:{jti}"

async def blacklist_access_token(payload: dict) -> None:
    """Marca o access token como revogado (logout).

    O access token é um JWT stateless válido por ~30 min. Sem blacklist,
    ele continua autenticando mesmo após o logout. Aqui gravamos o `jti`
    no Redis com TTL = tempo restante de validade do token.
    """
    jti = payload.get("jti")
    if not jti:
        return
    exp = payload.get("exp")
    now = int(datetime.now(timezone.utc).timestamp())
    ttl = max(1, int(exp) - now) if exp else 300
    r = _get_redis()
    await r.set(_access_blacklist_key(jti), "1", ex=ttl)

async def is_access_blacklisted(jti: str) -> bool:
    """True se o access token foi revogado no logout."""
    if not jti:
        return False
    r = _get_redis()
    return await r.exists(_access_blacklist_key(jti)) == 1

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
