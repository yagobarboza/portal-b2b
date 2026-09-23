"""Helpers de execução comuns aos processos de background."""

import os

from app.core.config import get_settings


def metrics_port() -> int:
    """Usa a porta injetada pelo Cloud Run, com fallback para ambiente local."""
    value = os.getenv("PORT") or str(get_settings().WORKER_METRICS_PORT)
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("PORT/WORKER_METRICS_PORT fora do intervalo valido.")
    return port
