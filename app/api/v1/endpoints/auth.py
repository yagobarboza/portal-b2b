"""Endpoints de autenticação (seções 8 e 57 do doc).
Segurança:
- Rate limit por IP no login (Redis, manual — sem dependência frágil do slowapi).
- Bloqueio progressivo por e-mail/IP (brute force).
- Mensagens genéricas: nunca revela se o e-mail existe.
- Access/Refresh tokens com rotação (seção 10).
- Cookies HttpOnly/Secure/SameSite.
- MFA (seção 11): login em DOIS passos quando o 2FA está ativo —
  1) senha correta → devolve desafio (challenge_token), SEM criar sessão;
  2) POST /auth/mfa/verify-login valida o código TOTP (ou recovery code)
     → só então cria a sessão.
- Desativação de MFA exige confirmação (senha atual OU código TOTP).
"""
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.cookies import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
)
from app.core.exceptions import UnauthorizedError
from app.core.login_guard import (
    check_rate_limit,
    clear_attempts,
    is_locked,
    register_failed_attempt,
)
from app.core.security import verify_password
from app.core.tokens import (
    TokenError,
    create_access_token,
    create_mfa_challenge_token,
    create_session,
    decode_token,
    revoke_mfa_challenge,
    validate_mfa_challenge,
    rotate_session,
    revoke_session,
    ACCESS_TYPE,
    REFRESH_TYPE,
)
from app.database.session import get_db
from app.repositories.user import UserRepository
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.mfa import (
    generate_recovery_codes,
    generate_secret,
    hash_recovery_codes,
    qr_code_data_uri,
    verify_recovery_code,
    verify_totp,
)
from app.core.password_recovery import consume_reset_token, create_reset_token
from app.core.security import hash_password, validate_password_strength
from app.core.tokens import revoke_all_sessions
from app.schemas.auth import (
    LoginRequest,
    MfaChallengeResponse,
    MfaDisableRequest,
    MfaLoginVerifyRequest,
    MfaSetupResponse,
    MfaVerifyRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RefreshRequest,
    TokenResponse,
    UserInfo,
)
from app.services.audit import record_audit
from app.core.permissions import effective_permissions

settings = get_settings()

router = APIRouter(tags=["auth"])

# Limite de tentativas do 2º fator por IP (anti brute-force do código TOTP).
MFA_VERIFY_RATE_LIMIT = 5
MFA_VERIFY_RATE_WINDOW = 60  # segundos

def _client_ip(request: Request) -> str:
    """IP do cliente, respeitando proxy reverso (X-Forwarded-For)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

@router.post(
    "/auth/login",
    response_model=TokenResponse | MfaChallengeResponse,
)
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse | MfaChallengeResponse:
    """Login com mensagem genérica — nunca revela se o e-mail existe.

    Se o usuário tiver MFA ativo, NÃO cria a sessão: devolve um desafio
    (`MfaChallengeResponse`) para o segundo fator ser validado em
    POST /auth/mfa/verify-login.
    """
    ip = _client_ip(request)
    user_agent = request.headers.get("user-agent")

    # Rate limit por IP (seção 8): 5 tentativas por minuto
    if await check_rate_limit(ip, limit=5, window=60):
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Muitas tentativas. Tente novamente mais tarde.",
                }
            },
        )

    # Bloqueio progressivo (brute force) — seção 8
    locked, _ = await is_locked(body.email, ip)
    if locked:
        raise UnauthorizedError("Credenciais inválidas.")

    users = UserRepository(db)
    user = await users.get_by_email(body.email)

    # Verificação genérica: mesmo erro para e-mail inexistente e senha errada
    valid = user is not None and verify_password(body.password, user.password_hash)
    if not valid:
        await register_failed_attempt(body.email, ip)
        # Auditoria: falha de login (sem revelar se o e-mail existe)
        await record_audit(
            db,
            action="login_failed",
            entity="user",
            ip=ip,
            user_agent=user_agent,
        )
        await db.commit()
        raise UnauthorizedError("Credenciais inválidas.")

    # ✅ MFA ATIVO: NÃO cria sessão — emite desafio de segundo fator.
    if user.mfa_enabled and user.mfa_secret_encrypted:
        challenge = await create_mfa_challenge_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            is_super_admin=user.is_super_admin,
        )
        await record_audit(
            db,
            action="login_mfa_challenge",
            entity="user",
            entity_id=user.id,
            user_id=user.id,
            tenant_id=user.tenant_id,
            ip=ip,
            user_agent=user_agent,
        )
        await db.commit()
        return MfaChallengeResponse(
            mfa_required=True,
            challenge_token=challenge,
            email=user.email,
        )

    # Login OK (sem MFA): limpa tentativas e cria sessão
    await clear_attempts(body.email, ip)
    session = await create_session(
        user_id=user.id,
        tenant_id=user.tenant_id,
        is_super_admin=user.is_super_admin,
    )
    access = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        is_super_admin=user.is_super_admin,
    )
    set_auth_cookies(response, access, session["refresh_token"])

    # Auditoria: login bem-sucedido
    await record_audit(
        db,
        action="login",
        entity="user",
        entity_id=user.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()
    return TokenResponse(
        access_token=access,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

@router.post("/auth/mfa/verify-login", response_model=TokenResponse)
async def mfa_verify_login(
    request: Request,
    body: MfaLoginVerifyRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Segundo fator do login: valida o código TOTP (ou recovery code).

    - Rate limit por IP (anti brute-force do código).
    - Aceita código TOTP do app autenticador OU um código de recuperação.
    - Se um recovery code for usado, ele é CONSUMIDO (uso único) e todos
      os demais são revogados (segurança padrão).
    - O desafio só é revogado (uso único) quando o 2º fator acerta.
    """
    ip = _client_ip(request)
    user_agent = request.headers.get("user-agent")

    # Rate limit por IP (mesmo padrão do login): 5 tentativas por minuto
    if await check_rate_limit(f"mfa:{ip}", limit=MFA_VERIFY_RATE_LIMIT, window=MFA_VERIFY_RATE_WINDOW):
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Muitas tentativas. Tente novamente mais tarde.",
                }
            },
        )

    # Valida o desafio (NÃO consome ainda)
    try:
        payload = await validate_mfa_challenge(body.challenge_token)
    except TokenError:
        raise UnauthorizedError("Desafio expirado. Faça login novamente.")

    users = UserRepository(db)
    user = await users.get(payload["sub"])

    if not user.mfa_enabled or not user.mfa_secret_encrypted:
        raise UnauthorizedError("MFA não está ativo para este usuário.")

    # ✅ Valida o 2º fator: TOTP primeiro; senão tenta recovery code.
    code_ok = verify_totp(user.mfa_secret_encrypted, body.code)
    used_recovery = False
    if not code_ok:
        # Tenta código de recuperação (uso único)
        used_recovery = await users.consume_mfa_recovery_code(user, body.code)
        code_ok = used_recovery

    if not code_ok:
        await record_audit(
            db,
            action="login_mfa_failed",
            entity="user",
            entity_id=user.id,
            user_id=user.id,
            tenant_id=user.tenant_id,
            ip=ip,
            user_agent=user_agent,
        )
        await db.commit()
        raise UnauthorizedError("Código inválido.")

    # ✅ 2º fator OK: consome o desafio e cria a sessão REAL
    await revoke_mfa_challenge(payload)
    await clear_attempts(user.email, ip)

    session = await create_session(
        user_id=user.id,
        tenant_id=user.tenant_id,
        is_super_admin=user.is_super_admin,
    )
    access = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        is_super_admin=user.is_super_admin,
    )
    set_auth_cookies(response, access, session["refresh_token"])

    # Auditoria: login bem-sucedido (com MFA)
    await record_audit(
        db,
        action="login",
        entity="user",
        entity_id=user.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()
    return TokenResponse(
        access_token=access,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    body: RefreshRequest,
    response: Response,
) -> TokenResponse:
    """Rotaciona o refresh token e emite novo access (seção 10)."""
    token = body.refresh_token or request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise UnauthorizedError("Sessão inválida.")
    try:
        payload = decode_token(token, REFRESH_TYPE)
        session_id = payload["sid"]
        new_refresh, _, stored = await rotate_session(session_id, payload["jti"])
    except (TokenError, KeyError):
        raise UnauthorizedError("Sessão inválida.")
    user_id = stored["user_id"]
    tenant_id = stored.get("tenant_id") or None
    is_super = stored.get("is_super_admin") == "1"
    access = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        is_super_admin=is_super,
    )
    set_auth_cookies(response, access, new_refresh)
    return TokenResponse(
        access_token=access,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

@router.post("/auth/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Revoga a sessão e limpa os cookies (seção 10)."""
    token = request.cookies.get(REFRESH_COOKIE)
    user_id = None
    if token:
        try:
            payload = decode_token(token, REFRESH_TYPE)
            await revoke_session(payload["sid"])
            user_id = payload.get("sub")
        except (TokenError, KeyError):
            pass  # sessão já inválida — logout é idempotente
    clear_auth_cookies(response)

    # Auditoria: logout
    await record_audit(
        db,
        action="logout",
        entity="user",
        entity_id=user_id,
        user_id=user_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return JSONResponse(status_code=200, content={"status": "ok"})

@router.get("/auth/me", response_model=UserInfo)
async def me(request: Request, db: AsyncSession = Depends(get_db)) -> UserInfo:
    """Retorna o usuário autenticado (valida a sessão no frontend)."""
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise UnauthorizedError("Não autenticado.")
    try:
        payload = decode_token(token, ACCESS_TYPE)
    except TokenError:
        raise UnauthorizedError("Não autenticado.")
    users = UserRepository(db)
    user = await users.get(payload["sub"])
    return UserInfo(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        tenant_id=user.tenant_id,
        is_super_admin=user.is_super_admin,
        mfa_enabled=user.mfa_enabled,
        customer_id=user.customer_id,
        roles=[r.slug for r in user.roles],
        permissions=sorted(effective_permissions(user)),
    )

# ===== MFA (seção 11) =====

@router.post("/auth/mfa/setup")
async def mfa_setup(request: Request, db: AsyncSession = Depends(get_db)):
    """Gera secret TOTP e QR code para ativar MFA.

    Os códigos de recuperação são gerados e armazenados (com hash) no
    usuário. O MFA só é efetivamente ATIVADO após POST /auth/mfa/verify.
    """
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise UnauthorizedError("Não autenticado.")
    try:
        payload = decode_token(token, ACCESS_TYPE)
    except TokenError:
        raise UnauthorizedError("Não autenticado.")
    users = UserRepository(db)
    user = await users.get(payload["sub"])
    if user.mfa_enabled:
        raise ForbiddenError("MFA já ativado.")

    secret = generate_secret()
    recovery_codes = generate_recovery_codes()
    # ✅ Armazena os códigos com hash (nunca em texto puro).
    await users.set_mfa_recovery_codes(user, hash_recovery_codes(recovery_codes))
    await db.commit()
    return {
        "secret": secret,
        "qr_code": qr_code_data_uri(secret, user.email),
        "recovery_codes": recovery_codes,
    }

@router.post("/auth/mfa/verify")
async def mfa_verify(
    request: Request,
    body: MfaVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """Confirma o código TOTP e ativa o MFA."""
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise UnauthorizedError("Não autenticado.")
    try:
        payload = decode_token(token, ACCESS_TYPE)
    except TokenError:
        raise UnauthorizedError("Não autenticado.")
    users = UserRepository(db)
    user = await users.get(payload["sub"])
    if not verify_totp(body.secret, body.code):
        raise UnauthorizedError("Código inválido.")
    await users.set_mfa(user, body.secret, enabled=True)

    # Auditoria: alteração de MFA
    await record_audit(
        db,
        action="mfa_change",
        entity="user",
        entity_id=user.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return {"status": "ok", "mfa_enabled": True}

@router.post("/auth/mfa/disable")
async def mfa_disable(
    request: Request,
    body: MfaDisableRequest,
    db: AsyncSession = Depends(get_db),
):
    """Desativa o MFA, exigindo confirmação (senha atual OU código TOTP).

    - Sem confirmação válida → nega (evita desativação por sessão roubada).
    - Limpa o secret, os códigos de recuperação e a flag mfa_enabled.
    """
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise UnauthorizedError("Não autenticado.")
    try:
        payload = decode_token(token, ACCESS_TYPE)
    except TokenError:
        raise UnauthorizedError("Não autenticado.")
    users = UserRepository(db)
    user = await users.get(payload["sub"])

    if not user.mfa_enabled:
        raise ForbiddenError("MFA não está ativado.")

    # Confirmação: senha atual OU código TOTP válido
    password_ok = bool(body.password) and verify_password(body.password, user.password_hash)
    code_ok = bool(body.code) and verify_totp(user.mfa_secret_encrypted or "", body.code)
    if not (password_ok or code_ok):
        raise UnauthorizedError("Confirmação inválida. Informe a senha atual ou um código válido.")

    # Desativa o MFA e limpa todos os dados associados
    await users.set_mfa(user, "", enabled=False)
    await users.clear_mfa_recovery_codes(user)

    # Auditoria: desativação de MFA
    await record_audit(
        db,
        action="mfa_disable",
        entity="user",
        entity_id=user.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return {"status": "ok", "mfa_enabled": False}

# ===== Recuperação de senha (seção 12) =====

@router.post("/auth/password/forgot")
async def password_forgot(
    body: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Solicita reset de senha. Mensagem genérica — não revela se o e-mail existe.

    Em produção, o token seria enviado por e-mail. Aqui retornamos
    apenas a confirmação genérica (o token fica no Redis).
    """
    users = UserRepository(db)
    user = await users.get_by_email(body.email)
    if user:
        await create_reset_token(user.id)
    # Mesma resposta para e-mail existente e inexistente (anti-enumeração)
    return {"status": "ok", "message": "Se o e-mail existir, você receberá um link de recuperação."}

@router.post("/auth/password/reset")
async def password_reset(
    request: Request,
    body: PasswordResetConfirmRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Confirma o reset com o token de uso único e define nova senha."""
    user_id = await consume_reset_token(body.token)
    if user_id is None:
        raise UnauthorizedError("Token inválido ou expirado.")
    validate_password_strength(body.new_password)
    users = UserRepository(db)
    user = await users.get(user_id)
    await users.update_password(user, hash_password(body.new_password))
    # Invalida todas as sessões (seção 12)
    await revoke_all_sessions(user.id)
    # Auditoria: alteração de senha
    await record_audit(
        db,
        action="password_change",
        entity="user",
        entity_id=user.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return {"status": "ok", "message": "Senha redefinida. Faça login novamente."}