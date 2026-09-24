"""Logs estruturados (seção 43 do doc).

Regras:
- Campos obrigatórios: timestamp, nível, serviço, request_id, evento.
- NUNCA logar: senhas, tokens, secrets, dados financeiros sensíveis,
  conteúdo privado de mensagens (seção 43).
"""
import logging
import sys
from typing import Any

import structlog

from app.core.config import get_settings
from app.core.redaction import structlog_redactor

settings = get_settings()

SENSITIVE_DEPENDENCY_LOGGERS = (
    "boto3",
    "botocore",
    "s3transfer",
    "urllib3",
)

# Processadores compartilhados
SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog_redactor,
]

def setup_logging() -> None:
    """Configura structlog + logging padrão (uma única vez)."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    structlog.configure(
        processors=[
            *SHARED_PROCESSORS,
            # Em produção: JSON; em dev: console legível
            (
                structlog.processors.JSONRenderer()
                if settings.APP_ENV == "production"
                else structlog.dev.ConsoleRenderer()
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Alinha o logging padrão (uvicorn, sqlalchemy) ao structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )
    logging.getLogger().setLevel(log_level)

    # SDKs HTTP podem incluir identificadores de credencial, assinaturas e
    # headers nos eventos DEBUG. Nunca permita esse nível em produção, mesmo
    # que uma biblioteca tenha configurado seu logger antes da aplicação.
    dependency_level = max(log_level, logging.WARNING)
    for logger_name in SENSITIVE_DEPENDENCY_LOGGERS:
        logging.getLogger(logger_name).setLevel(dependency_level)

def get_logger(name: str):
    """Retorna um logger structlog com o serviço identificado."""
    return structlog.get_logger(service=name)
