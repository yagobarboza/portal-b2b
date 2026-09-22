"""Redação defensiva compartilhada por logs, runs e Sentry."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"
MAX_SAFE_TEXT = 500
_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|senha|secret|token|authorization|cookie|api[_-]?key|signature|credential|recovery|access[_-]?key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_SECRET_PREFIX = re.compile(r"\b(?:whsec|int|sk|pk)_[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_DOCUMENT = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)|(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|key|secret|signature|password)=)[^&\s]+"
)


def redact_text(value: Any, *, limit: int = MAX_SAFE_TEXT) -> str:
    text = str(value)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _JWT.sub(REDACTED, text)
    text = _SECRET_PREFIX.sub(REDACTED, text)
    text = _EMAIL.sub(REDACTED, text)
    text = _DOCUMENT.sub(REDACTED, text)
    text = _QUERY_SECRET.sub(r"\1[REDACTED]", text)
    return text[:limit]


def redact_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        output = {}
        for raw_key, item in list(value.items())[:100]:
            key = str(raw_key)[:100]
            output[key] = (
                REDACTED
                if _SENSITIVE_KEY.search(key)
                else redact_value(item, depth=depth + 1)
            )
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (bytes, bytearray)):
        return f"[BINARY {len(value)} bytes]"
    return value


def redact_item_errors(
    details: list[dict] | None, *, limit: int
) -> tuple[list[dict], int]:
    """Mantém apenas campos diagnósticos conhecidos e nunca o registro bruto."""
    source = details or []
    safe: list[dict] = []
    allowed = {"row", "index", "sku", "error", "code"}
    for detail in source[:limit]:
        if not isinstance(detail, dict):
            safe.append({"error": redact_text(detail)})
            continue
        item = {}
        for key in allowed.intersection(detail):
            value = detail[key]
            if key in {"row", "index"} and isinstance(value, int):
                item[key] = value
            else:
                item[key] = redact_text(value, limit=200 if key == "error" else 80)
        safe.append(item or {"error": "Erro de item sem detalhe seguro."})
    return safe, max(0, len(source) - len(safe))


def structlog_redactor(_logger, _method_name: str, event_dict: dict) -> dict:  # noqa: ANN001
    return redact_value(event_dict)
