"""Observabilidade (Bloco 13 — seção 46).

- Prometheus: métricas HTTP via prometheus-fastapi-instrumentator.
- Sentry: captura de exceções (só se SENTRY_DSN configurado).
- Nunca envia dados sensíveis (seção 43): redact de tokens/cookies/senhas.
"""
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("monitoring")

instrumentator = Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
)

def init_sentry() -> None:
    """Inicializa o Sentry apenas se SENTRY_DSN estiver configurado."""
    settings = get_settings()
    if not settings.SENTRY_DSN:
        logger.info("sentry_disabled_no_dsn")
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            integrations=[
                StarletteIntegration(transaction_style="endpoint"),
                FastApiIntegration(transaction_style="endpoint"),
            ],
            send_default_pii=False,  # nunca enviar dados pessoais (seção 43)
            before_send=_before_send,
        )
        logger.info("sentry_enabled")
    except Exception:  # noqa: BLE001 — observabilidade nunca derruba a app
        logger.exception("sentry_init_failed")

def _before_send(event, hint):  # noqa: ANN001
    """Remove dados sensíveis antes de enviar ao Sentry (seção 43)."""
    try:
        request = event.get("request", {})
        headers = request.get("headers", {})
        sensitive = ("authorization", "cookie", "x-api-key", "x-webhook-signature")
        for key in sensitive:
            if key in headers:
                headers[key] = "[REDACTED]"
            if key in request:
                request[key] = "[REDACTED]"
        data = request.get("data")
        if isinstance(data, str) and data.lower() and (
            "password" in data.lower() or "senha" in data.lower()
        ):
            request["data"] = "[REDACTED]"
    except Exception:  # noqa: BLE001
        pass
    return event

def setup_metrics(app) -> None:  # noqa: ANN001
    """Liga as métricas Prometheus no app e expõe GET /metrics."""
    instrumentator.instrument(app).expose(
        app, endpoint="/metrics", include_in_schema=False
    )
    logger.info("metrics_enabled_at_/metrics")