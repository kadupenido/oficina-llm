import json

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import bearer, get_chat_service, require_api_key
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ErrorBody,
    MessageOut,
    StreamMeta,
    StreamTitle,
    StreamToolEnd,
    StreamToolStart,
)
from app.services.chat import ChatService, TurnResult

router = APIRouter(tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

ERROR_RESPONSES = {
    401: {"model": ErrorBody, "description": "API key ausente ou inválida."},
    404: {"model": ErrorBody, "description": "Conversa não encontrada."},
    409: {"model": ErrorBody, "description": "Já existe uma geração nesta conversa."},
    413: {"model": ErrorBody, "description": "Mensagem maior que o contexto."},
    422: {"model": ErrorBody, "description": "Corpo inválido."},
    503: {"model": ErrorBody, "description": "llama-server indisponível ou sem slot livre."},
    504: {"model": ErrorBody, "description": "Tempo esgotado ao falar com o modelo."},
}


def _message_out(result: TurnResult) -> ChatResponse:
    message = result.message
    return ChatResponse(
        conversation_id=result.conversation_id,
        title=result.title,
        history_truncated=result.history_truncated,
        finish_reason=result.finish_reason,
        message=MessageOut(
            id=message.id,
            role=message.role,
            content=message.content,
            status=message.status,
            created_at=message.created_at,
        ),
    )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _authorized(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    await require_api_key(request, credentials)


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Envia uma mensagem e recebe a resposta completa",
    responses=ERROR_RESPONSES,
)
async def chat(
    body: ChatRequest,
    _: None = Depends(_authorized),
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    structlog.contextvars.bind_contextvars(conversation_id=body.conversation_id or "new")
    turn = await service.prepare(
        body.conversation_id,
        body.message,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        regenerate=body.regenerate,
        attachment_ids=body.attachment_ids,
    )
    result = await service.complete(turn)
    return _message_out(result)


@router.post(
    "/chat/stream",
    summary="Envia uma mensagem e recebe a resposta em Server-Sent Events",
    description=(
        "Eventos `meta` (StreamMeta), `think` (StreamThink), `tool_start` (StreamToolStart), "
        "`tool_end` (StreamToolEnd), `token` (StreamToken), `done` (StreamDone), "
        "`title` (StreamTitle) e `error` (StreamError). Comentários `: ping` "
        "mantêm o stream aberto enquanto o modelo processa o prompt. "
        "O corpo é o mesmo de POST /api/chat."
    ),
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Stream SSE. Cada evento termina com uma linha em branco.",
            "content": {
                "text/event-stream": {
                    "example": (
                        "event: meta\n"
                        'data: {"conversation_id":"...","user_message_id":"...","title":"...","context_tokens":10,"context_limit":65536}\n\n'
                        "event: think\n"
                        'data: {"delta":"..."}\n\n'
                        "event: token\n"
                        'data: {"delta":"Olá"}\n\n'
                        "event: done\n"
                        'data: {"message_id":"...","content":"Olá","status":"complete","title":"...","history_truncated":false,"finish_reason":"stop","context_tokens":12,"tokens_per_second":56.4,"prompt_tokens":10,"completion_tokens":2,"thinking":"..."}\n\n'
                        "event: title\n"
                        'data: {"conversation_id":"...","title":"..."}\n\n'
                    )
                }
            },
        },
        **ERROR_RESPONSES,
    },
)
async def chat_stream(
    body: ChatRequest,
    request: Request,
    _: None = Depends(_authorized),
    service: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    structlog.contextvars.bind_contextvars(conversation_id=body.conversation_id or "new")
    turn = await service.prepare(
        body.conversation_id,
        body.message,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        regenerate=body.regenerate,
        attachment_ids=body.attachment_ids,
    )

    async def events():
        try:
            yield _sse(
                "meta",
                StreamMeta(
                    conversation_id=turn.conversation_id,
                    user_message_id=turn.user_message_id,
                    title=turn.title,
                    context_tokens=turn.context_tokens,
                    context_limit=turn.context_limit,
                ).model_dump(),
            )
            async for kind, payload in service.generate_stream(turn, request):
                if kind == "ping":
                    yield ": ping\n\n"
                    continue
                if kind == "title":
                    yield _sse(kind, StreamTitle(**payload).model_dump())
                    continue
                if kind == "tool_start":
                    yield _sse(kind, StreamToolStart(**payload).model_dump())
                    continue
                if kind == "tool_end":
                    yield _sse(kind, StreamToolEnd(**payload).model_dump())
                    continue
                yield _sse(kind, payload)
        finally:
            service.release(turn)

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)
