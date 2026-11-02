from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select

from app.api.deps import bearer, require_api_key
from app.db.models import Conversation, Message
from app.schemas.attachments import AttachmentSummary
from app.schemas.chat import ErrorBody, MessageOut
from app.schemas.conversation import ConversationDetail, ConversationList, ConversationSummary, RenameRequest
from app.services.attachments import compose_user_content, load_attachments_for_messages
from app.services.chat import utcnow
from app.services.context import prompt_messages, prompt_token_estimate

router = APIRouter(tags=["conversations"])

ERROR_RESPONSES = {
    401: {"model": ErrorBody},
    404: {"model": ErrorBody},
    422: {"model": ErrorBody},
}


async def _authorized(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    await require_api_key(request, credentials)


def _parse_id(conversation_id: str) -> str:
    try:
        return str(UUID(conversation_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Identificador inválido.") from exc


def _summary(conversation: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get(
    "/conversations",
    response_model=ConversationList,
    summary="Lista conversas",
    responses=ERROR_RESPONSES,
)
async def list_conversations(
    request: Request,
    _: None = Depends(_authorized),
) -> ConversationList:
    async with request.app.state.sessionmaker() as session:
        rows = (
            await session.scalars(select(Conversation).order_by(Conversation.updated_at.desc()))
        ).all()
    return ConversationList(conversations=[_summary(row) for row in rows])


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
    summary="Lê uma conversa e as mensagens",
    responses=ERROR_RESPONSES,
)
async def get_conversation(
    conversation_id: str,
    request: Request,
    _: None = Depends(_authorized),
) -> ConversationDetail:
    conversation_id = _parse_id(conversation_id)
    async with request.app.state.sessionmaker() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversa não encontrada.")
        messages = (
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at.asc(), Message.id.asc())
            )
        ).all()
        attachment_map = await load_attachments_for_messages(
            session, [item.id for item in messages if item.role == "user"]
        )
    settings = request.app.state.settings
    history_for_estimate: list[dict[str, str]] = []
    for item in messages:
        row = {"role": item.role, "content": item.content, "status": item.status}
        if item.role == "user":
            row["content"] = compose_user_content(
                item.content, attachment_map.get(item.id, [])
            )
        history_for_estimate.append(row)
    occupied = [
        {
            "role": "system",
            "content": settings.effective_system_prompt(conversation.system_prompt),
        },
        *prompt_messages(history_for_estimate),
    ]
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        context_tokens=prompt_token_estimate(
            occupied,
            settings.chars_per_token,
            settings.message_overhead_tokens,
        ),
        context_limit=settings.llama_ctx_size,
        messages=[
            MessageOut(
                id=item.id,
                role=item.role,
                content=item.content,
                status=item.status,
                created_at=item.created_at,
                attachments=[
                    AttachmentSummary(id=att.id, filename=att.filename)
                    for att in attachment_map.get(item.id, [])
                ],
            )
            for item in messages
        ],
    )


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationSummary,
    summary="Renomeia uma conversa",
    responses=ERROR_RESPONSES,
)
async def rename_conversation(
    conversation_id: str,
    body: RenameRequest,
    request: Request,
    _: None = Depends(_authorized),
) -> ConversationSummary:
    conversation_id = _parse_id(conversation_id)
    async with request.app.state.sessionmaker() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversa não encontrada.")
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="O título não pode ficar em branco.")
        conversation.title = title
        conversation.updated_at = utcnow()
        await session.commit()
        await session.refresh(conversation)
        summary = _summary(conversation)
    return summary


@router.delete(
    "/conversations/{conversation_id}",
    status_code=204,
    summary="Apaga uma conversa e as mensagens",
    responses=ERROR_RESPONSES,
)
async def delete_conversation(
    conversation_id: str,
    request: Request,
    _: None = Depends(_authorized),
) -> Response:
    conversation_id = _parse_id(conversation_id)
    async with request.app.state.sessionmaker() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversa não encontrada.")
        await session.delete(conversation)
        await session.commit()
        remaining = await session.scalar(
            select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
        )
    if remaining:
        raise HTTPException(status_code=500, detail="Falha ao apagar as mensagens.")
    return Response(status_code=204)
