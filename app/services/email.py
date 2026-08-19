"""Envio de e-mails transacionais via Resend (httpx — já no requirements)."""
import httpx

from app.core.config import get_settings

async def send_invite_email(
    *,
    to_email: str,
    invite_url: str,
    company_name: str,
    expires_hours: int,
) -> bool:
    """Envia o e-mail de convite. Retorna False se o Resend não estiver configurado."""
    settings = get_settings()
    if not settings.RESEND_API_KEY:
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
    return True