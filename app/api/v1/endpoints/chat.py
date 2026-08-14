"""Endpoints de chat (Bloco 8 — seções 23, 24, 25).

- REST: salas, mensagens (histórico), status de leitura, transferência, anexos.
- WebSocket: /ws/{room_id}?token=... com validação de token/tenant/sala/
  permissão (seção 25) e Redis Pub/Sub para broadcast entre instâncias.
"""
import asyncio
from uuid import UUID

from fastapi import (APIRouter, Depends, File, UploadFile, WebSocket,
                     WebSocketDisconnect)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import ForbiddenError
from app.core.tokens import ACCESS_TYPE, TokenError, decode_token
from app.database.session import async_session_factory, get_db
from app.models import User
from app.repositories.chat import ChatRepository
from app.repositories.user import UserRepository
from app.schemas.chat import (ChatMessageCreate, ChatMessagePage,
                              ChatMessageRead, ChatRoomRead,
                              ChatTransferRequest)
from app.services.chat import (_msg_payload, get_chat_room_for_user,
                               publish_chat_message, redis_client,
                               send_chat_message)

router = APIRouter(prefix="/chat", tags=["Chat"])

# ---------- Salas ----------
@router.get("/rooms", response_model=list[ChatRoomRead])
async def list_rooms(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ChatRoomRead]:
    """Cliente: suas salas. Atendente: salas do tenant (seção 23)."""
    repo = ChatRepository(db)
    if user.customer_id:
        return await repo.list_rooms_by_customer(user.customer_id)
    return await repo.list_rooms_by_tenant()

@router.post("/rooms", response_model=ChatRoomRead, status_code=201)
async def get_or_create_room(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatRoomRead:
    """Cliente: cria (ou obtém) a sala automática (seção 23)."""
    if not user.customer_id:
        raise ForbiddenError("Acesso negado.")
    repo = ChatRepository(db)
    room = await repo.get_or_create_room(user.customer_id)
    await db.commit()
    return room

# ---------- Mensagens (REST) ----------
@router.get("/rooms/{room_id}/messages", response_model=ChatMessagePage)
async def list_messages(
    room_id: UUID,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatMessagePage:
    room = await get_chat_room_for_user(db, user, room_id)
    repo = ChatRepository(db)
    items, total = await repo.list_messages(room.id, page, page_size)
    return ChatMessagePage(items=items, total=total, page=page, page_size=page_size)

@router.post("/rooms/{room_id}/messages", response_model=ChatMessageRead, status_code=201)
async def send_message(
    room_id: UUID,
    body: ChatMessageCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatMessageRead:
    room = await get_chat_room_for_user(db, user, room_id)
    return await send_chat_message(db, room, user, body.content)

@router.post("/rooms/{room_id}/read")
async def mark_read(
    room_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Marca como lidas as mensagens do outro lado (seção 23)."""
    room = await get_chat_room_for_user(db, user, room_id)
    repo = ChatRepository(db)
    updated = await repo.mark_room_read(room.id, user)
    await db.commit()
    return {"updated": updated}

@router.post("/rooms/{room_id}/transfer", response_model=ChatRoomRead)
async def transfer_room(
    room_id: UUID,
    body: ChatTransferRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatRoomRead:
    """Transferência de setor do atendimento (apenas atendente, seção 23)."""
    if user.customer_id:
        raise ForbiddenError("Acesso negado.")
    room = await get_chat_room_for_user(db, user, room_id)
    repo = ChatRepository(db)
    room = await repo.transfer_room(room.id, body.sector, user.id)
    msg = await repo.create_message(
        room.id, "system", user.id, None,
        f"Sala transferida para o setor {body.sector.value}",
    )
    await db.commit()
    await publish_chat_message(room.id, msg)
    return room

# ---------- Anexos (R2 — reutiliza Bloco 6, seção 19) ----------
@router.post("/rooms/{room_id}/attachments", response_model=ChatMessageRead, status_code=201)
async def upload_attachment(
    room_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatMessageRead:
    """Upload de anexo de chat: validação (Bloco 6) -> R2 -> metadados -> mensagem."""
    room = await get_chat_room_for_user(db, user, room_id)

    # Import lazy para não quebrar o startup caso os nomes do Bloco 6 divirjam
    from app.repositories.file import FileRepository
    from app.services.file_validation import validate_uploaded_file
    from app.services.storage import upload_to_r2

    # 1) Validação do arquivo (seção 19: tamanho/extensão/MIME/conteúdo)
    validated = await validate_uploaded_file(file)  # (nome_sanitizado, mime, size)
    # 2) Upload para o R2 (seção 18) — chave = UUID, nunca nome do usuário
    object_key = await upload_to_r2(file, owner_type="chat", owner_id=room.id, tenant_id=user.tenant_id)
    # 3) Metadados no PostgreSQL
    file_repo = FileRepository(db)
    file_row = await file_repo.create(
        tenant_id=user.tenant_id,
        owner_type="chat",
        owner_id=room.id,
        name=validated[0],
        mime_type=validated[1],
        size_bytes=validated[2],
        storage_key=object_key,
    )
    # 4) Mensagem com anexo
    msg = await send_chat_message(db, room, user, "📎 Anexo", file_row.id)
    return msg

# ---------- WebSocket (seção 25) ----------
@router.websocket("/ws/{room_id}")
async def chat_websocket(websocket: WebSocket, room_id: UUID):
    """Chat em tempo real: token -> tenant -> sala -> permissão.

    Um usuário de um tenant NUNCA ingressa em sala de outro tenant (seção 25).
    """
    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=4401, reason="Não autenticado")
        return
    try:
        payload = decode_token(token, ACCESS_TYPE)
    except TokenError:
        await websocket.close(code=4401, reason="Não autenticado")
        return

    async with async_session_factory() as db:
        users = UserRepository(db)
        user = await users.get(payload["sub"])
        if user is None or user.status.value != "active":
            await websocket.close(code=4401, reason="Não autenticado")
            return

        # Isolamento por tenant (seção 5)
        from app.core.context import TenantContext
        TenantContext.set(
            tenant_id=user.tenant_id,
            user_id=user.id,
            is_super_admin=user.is_super_admin,
        )

        repo = ChatRepository(db)
        room = await repo.get_room(room_id)
        if not room:
            await websocket.close(code=4404, reason="Sala não encontrada")
            return

        # Validação de permissão (seção 25)
        allowed = (
            user.is_super_admin
            or (user.customer_id and room.customer_id == user.customer_id)
            or (user.tenant_id and not user.customer_id and room.tenant_id == user.tenant_id)
        )
        if not allowed:
            await websocket.close(code=4403, reason="Acesso negado")
            return

        await websocket.accept()

        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"chat:{room_id}")

        async def _listen_redis():
            try:
                async for message in pubsub.listen():
                    if message.get("type") == "message":
                        await websocket.send_text(message["data"])
            except Exception:
                pass

        listener = asyncio.create_task(_listen_redis())
        try:
            while True:
                raw = await websocket.receive_json()
                content = (raw.get("content") or "").strip()
                if not content:
                    continue
                attachment_file_id = raw.get("attachment_file_id")
                msg = await send_chat_message(
                    db, room, user, content, attachment_file_id
                )
                # Broadcast via Redis Pub/Sub (todos os conectados recebem)
                await publish_chat_message(room.id, msg)
        except WebSocketDisconnect:
            pass
        finally:
            listener.cancel()
            try:
                await pubsub.unsubscribe()
            except Exception:
                pass