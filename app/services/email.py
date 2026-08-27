"""Envio de e-mails transacionais via Resend (httpx — já no requirements).

Regra de ouro: este módulo NUNCA lança exceção para o chamador.
Falha no envio de e-mail não pode derrubar a criação de empresa/convite.
"""
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

async def send_invite_email(
    *,
    to_email: str,
    invite_url: str,
    company_name: str,
    expires_hours: int,
) -> bool:
    """Envia o e-mail de convite.

    Retorna True se enviado com sucesso, False se o Resend não estiver
    configurado OU se a chamada falhar (erro de API, rede, etc.).
    """
    settings = get_settings()
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY não configurada — convite não enviado por e-mail.")
        return False

    subject = f"Convite de acesso — {company_name}"
    html = f"""
<div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
  <h2 style="color: #111;">Você foi convidado para o portal {company_name}</h2>
  <p style="color: #333; font-size: 15px; line-height: 1.5;">
    Para concluir seu cadastro, clique no botão abaixo e defina sua senha.
  </p>
  <p style="text-align: center; margin: 32px 0;">
    <a href="{invite_url}"
       style="background: #111; color: #fff; padding: 14px 28px;
              border-radius: 8px; text-decoration: none; font-weight: bold;">
      Aceitar convite
    </a>
  </p>
  <p style="color: #777; font-size: 13px;">
    O link é válido por {expires_hours} horas. Se você não solicitou este convite,
    ignore este e-mail.
  </p>
</div>
"""
    text = (
        f"Você foi convidado para acessar o portal {company_name}. "
        f"Clique no link para definir sua senha: {invite_url} "
        f"(válido por {expires_hours} horas)."
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json={
                    "from": settings.RESEND_FROM_EMAIL,
                    "to": [to_email],
                    "subject": subject,
                    "html": html,
                    "text": text,
                },
            )
            resp.raise_for_status()
    except Exception:
        # Falha no e-mail NÃO deve derrubar a criação da empresa/convite.
        logger.exception("Falha ao enviar e-mail de convite para %s", to_email)
        return False
    return True

async def send_company_status_email(
    *,
    to_email: str,
    company_name: str,
    status: str,  # "active" | "inactive"
) -> bool:
    """Notifica o admin sobre ativação/inativação da empresa (NYD B2B).

    Retorna True se enviado, False se o Resend não estiver configurado
    ou se a chamada falhar (fail-safe, nunca lança exceção).
    """
    settings = get_settings()
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY não configurada — e-mail de status não enviado.")
        return False

    if status == "active":
        subject = f"Sua empresa foi ativada — {company_name} · NYD B2B"
        heading = "Sua empresa foi ativada!"
        message = (
            f"Ótima notícia! A empresa <strong>{company_name}</strong> foi ativada "
            f"no <strong>NYD B2B</strong>. O acesso dos usuários foi restaurado e "
            f"os dados do tenant voltaram a ficar visíveis normalmente."
        )
    else:
        subject = f"Sua empresa foi inativada — {company_name} · NYD B2B"
        heading = "Sua empresa foi inativada"
        message = (
            f"Informamos que a empresa <strong>{company_name}</strong> foi inativada "
            f"no <strong>NYD B2B</strong>. O acesso dos usuários foi bloqueado e os "
            f"dados do tenant ficaram ocultos. Para mais informações, entre em "
            f"contato com a nossa equipe."
        )

    html = f"""
<div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto; border: 1px solid #eee; border-radius: 12px; overflow: hidden;">
  <div style="background: #111; color: #fff; padding: 24px; text-align: center;">
    <span style="font-size: 20px; font-weight: 700; letter-spacing: 0.04em;">NYD B2B</span>
  </div>
  <div style="padding: 28px;">
    <h2 style="color: #111; margin-top: 0;">{heading}</h2>
    <p style="color: #333; font-size: 15px; line-height: 1.6;">{message}</p>
    <p style="color: #999; font-size: 12px; margin-top: 32px;">Este é um e-mail automático do NYD B2B. Não responda a esta mensagem.</p>
  </div>
</div>
"""

    # Remove as tags HTML para a versão em texto puro.
    # Calculado FORA da f-string: o Python 3.11 não permite backslash
    # dentro da parte {…} de uma f-string (SyntaxError).
    plain_message = (
        message.replace("<strong>", "")
        .replace("</strong>", "")
        .replace("<br>", "\n")
    )
    text = (
        f"NYD B2B — {heading}\n\n"
        f"{plain_message}\n\n"
        f"Este é um e-mail automático do NYD B2B."
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json={
                    "from": settings.RESEND_FROM_EMAIL,
                    "to": [to_email],
                    "subject": subject,
                    "html": html,
                    "text": text,
                },
            )
            resp.raise_for_status()
    except Exception:
        logger.exception("Falha ao enviar e-mail de status para %s", to_email)
        return False
    return True

async def send_customer_invite_email(
    *,
    to_email: str,
    invite_url: str,
    company_name: str,
    expires_hours: int,
) -> bool:
    """Convida um cliente a acessar o portal (NYD B2B).

    Retorna True se enviado, False se o Resend não estiver configurado
    ou se a chamada falhar (fail-safe, nunca lança exceção).
    """
    settings = get_settings()
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY não configurada — convite de cliente não enviado.")
        return False

    subject = f"Você foi convidado para o portal {company_name} · NYD B2B"
    html = f"""
<div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto; border: 1px solid #eee; border-radius: 12px; overflow: hidden;">
  <div style="background: #111; color: #fff; padding: 24px; text-align: center;">
    <span style="font-size: 20px; font-weight: 700; letter-spacing: 0.04em;">NYD B2B</span>
  </div>
  <div style="padding: 28px;">
    <h2 style="color: #111; margin-top: 0;">Bem-vindo ao portal {company_name}!</h2>
    <p style="color: #333; font-size: 15px; line-height: 1.6;">
      Sua empresa criou um acesso para você no portal <strong>{company_name}</strong>.
      Para começar, clique no botão abaixo e defina sua senha.
    </p>
    <p style="text-align: center; margin: 32px 0;">
      <a href="{invite_url}"
         style="background: #111; color: #fff; padding: 14px 28px;
                border-radius: 8px; text-decoration: none; font-weight: bold;">
        Criar minha senha
      </a>
    </p>
    <p style="color: #777; font-size: 13px;">
      O link é válido por {expires_hours} horas. Se você não esperava este convite,
      ignore este e-mail.
    </p>
  </div>
</div>
"""
    text = (
        f"Bem-vindo ao portal {company_name}! Sua empresa criou um acesso para você. "
        f"Clique no link para definir sua senha: {invite_url} "
        f"(válido por {expires_hours} horas)."
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json={
                    "from": settings.RESEND_FROM_EMAIL,
                    "to": [to_email],
                    "subject": subject,
                    "html": html,
                    "text": text,
                },
            )
            resp.raise_for_status()
    except Exception:
        logger.exception("Falha ao enviar convite de cliente para %s", to_email)
        return False
    return True