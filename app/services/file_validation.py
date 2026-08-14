"""Validação de upload de arquivos (seção 19).

Nunca confiar apenas na extensão — validar tamanho, extensão, MIME type
e conteúdo (magic bytes). Bloquear formatos executáveis.
"""
import magic

from app.core.exceptions import ValidationError
from app.models.enums import FileOwnerType

ALLOWED_BY_OWNER: dict[FileOwnerType, dict] = {
    FileOwnerType.PRODUCT: {
        "extensions": {".jpg", ".jpeg", ".png", ".webp"},
        "mime_types": {"image/jpeg", "image/png", "image/webp"},
        "max_size": 5 * 1024 * 1024,
    },
    FileOwnerType.CATALOG: {
        "extensions": {".jpg", ".jpeg", ".png", ".webp"},
        "mime_types": {"image/jpeg", "image/png", "image/webp"},
        "max_size": 5 * 1024 * 1024,
    },
    FileOwnerType.TICKET: {
        "extensions": {".jpg", ".jpeg", ".png", ".webp", ".pdf"},
        "mime_types": {"image/jpeg", "image/png", "image/webp", "application/pdf"},
        "max_size": 5 * 1024 * 1024,
    },
    FileOwnerType.CHAT: {
        "extensions": {".jpg", ".jpeg", ".png", ".pdf"},
        "mime_types": {"image/jpeg", "image/png", "application/pdf"},
        "max_size": 5 * 1024 * 1024,
    },
    FileOwnerType.DOCUMENT: {
        "extensions": {".pdf", ".docx", ".jpg", ".jpeg", ".png"},
        "mime_types": {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "image/jpeg",
            "image/png",
        },
        "max_size": 5 * 1024 * 1024,
    },
    FileOwnerType.USER: {
        "extensions": {".jpg", ".jpeg", ".png"},
        "mime_types": {"image/jpeg", "image/png"},
        "max_size": 5 * 1024 * 1024,
    },
}

BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".sh", ".ps1", ".js", ".php", ".py",
    ".html", ".htm", ".svg", ".xml", ".dll", ".so", ".bin", ".apk",
    ".jar", ".msi", ".com", ".scr", ".vbs",
}

def validate_upload(
    *,
    filename: str,
    content: bytes,
    owner_type: FileOwnerType,
) -> tuple[str, str, int]:
    """Valida um upload e retorna (extensão, mime_type, size_bytes)."""
    rules = ALLOWED_BY_OWNER.get(owner_type)
    if not rules:
        raise ValidationError("Finalidade de upload inválida.")

    size = len(content)
    if size > rules["max_size"]:
        raise ValidationError(
            f"Arquivo excede o limite de {rules['max_size'] // (1024*1024)} MB."
        )
    if size == 0:
        raise ValidationError("Arquivo vazio.")

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in rules["extensions"]:
        raise ValidationError("Extensão de arquivo não permitida para esta finalidade.")
    if ext in BLOCKED_EXTENSIONS:
        raise ValidationError("Tipo de arquivo bloqueado.")

    detected = magic.from_buffer(content, mime=True)
    if detected not in rules["mime_types"]:
        raise ValidationError(
            f"Conteúdo do arquivo não corresponde ao tipo permitido (detectado: {detected})."
        )

    return ext, detected, size