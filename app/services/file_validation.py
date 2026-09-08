"""Validação de upload de arquivos (seção 19).

Nunca confiar apenas na extensão — validar tamanho, extensão, MIME type
e conteúdo (magic bytes). Bloquear formatos executáveis.

✅ CORREÇÃO (500 no upload): a validação de MIME não depende mais da
biblioteca libmagic (`magic`), que frequentemente não existe no container
e fazia `magic.from_buffer()` estourar com 500. Agora o MIME é detectado
manualmente pelos magic bytes padrão de cada formato permitido.
"""
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

def _detect_mime(content: bytes) -> str | None:
    """Detecta o MIME pelos magic bytes (sem libmagic).

    Cobre exatamente os formatos permitidos pelo sistema:
    JPEG (FF D8 FF), PNG (89 50 4E 47), WebP (RIFF...WEBP),
    PDF (%PDF), DOCX (PK... [Content_Types].xml).
    """
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if (
        content[:4] == b"RIFF"
        and len(content) >= 12
        and content[8:12] == b"WEBP"
    ):
        return "image/webp"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if (
        content[:2] == b"PK"
        and b"[Content_Types].xml" in content[:4096]
    ):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return None

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
            f"Arquivo excede o limite de {rules['max_size'] // (1024 * 1024)} MB."
        )
    if size == 0:
        raise ValidationError("Arquivo vazio.")

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in rules["extensions"]:
        raise ValidationError("Extensão de arquivo não permitida para esta finalidade.")
    if ext in BLOCKED_EXTENSIONS:
        raise ValidationError("Tipo de arquivo bloqueado.")

    detected = _detect_mime(content)
    if detected is None or detected not in rules["mime_types"]:
        raise ValidationError(
            "Conteúdo do arquivo não corresponde ao tipo permitido "
            "(verifique se o arquivo está íntegro e não foi renomeado)."
        )

    return ext, detected, size