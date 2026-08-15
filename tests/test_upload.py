"""Upload malicioso (seções 19/52).

Anexo inválido em ticket deve ser bloqueado (4xx) — nunca aceito (201).
O endpoint /tickets/{id}/attachments valida via validate_upload (Bloco 6).
"""
import io

from tests.conftest import create_ticket

async def _upload(client, ticket_id, filename, content, content_type):
    files = {"file": (filename, io.BytesIO(content), content_type)}
    return await client.post(f"/tickets/{ticket_id}/attachments", files=files)

async def test_upload_exe_bloqueado(client_cliente):
    ticket_id = await create_ticket(client_cliente, "Upload seguro", "teste")
    r = await _upload(
        client_cliente, ticket_id, "malware.exe",
        b"MZ....", "application/octet-stream",
    )
    assert r.status_code in (400, 415, 422), f".exe foi aceito! ({r.status_code})"

async def test_upload_jpg_falso_bloqueado(client_cliente):
    """Texto puro renomeado para .jpg — validação de conteúdo deve barrar."""
    ticket_id = await create_ticket(client_cliente, "Upload seguro", "teste")
    r = await _upload(
        client_cliente, ticket_id, "fake.jpg",
        b"isto nao e uma imagem" * 10, "image/jpeg",
    )
    assert r.status_code in (400, 415, 422), f".jpg falso foi aceito! ({r.status_code})"