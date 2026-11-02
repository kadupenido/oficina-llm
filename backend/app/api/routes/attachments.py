from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import bearer, require_api_key
from app.errors import AppError
from app.schemas.attachments import AttachmentUploaded, UploadAttachmentsResponse
from app.schemas.chat import ErrorBody
from app.services.attachments import create_pending_attachments, ensure_conversation_for_upload

router = APIRouter(tags=["attachments"])

ERROR_RESPONSES = {
    401: {"model": ErrorBody},
    404: {"model": ErrorBody},
    413: {"model": ErrorBody},
    422: {"model": ErrorBody},
    503: {"model": ErrorBody},
}


async def _authorized(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    await require_api_key(request, credentials)


def _parse_conversation_id(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    try:
        return str(UUID(value.strip()))
    except ValueError as exc:
        raise AppError(422, "conversation_id deve ser um UUID.") from exc


@router.post(
    "/attachments",
    response_model=UploadAttachmentsResponse,
    summary="Envia arquivos e extrai texto",
    responses=ERROR_RESPONSES,
)
async def upload_attachments(
    request: Request,
    files: list[UploadFile] = File(...),
    conversation_id: str | None = Form(default=None),
    _: None = Depends(_authorized),
) -> UploadAttachmentsResponse:
    settings = request.app.state.settings
    parsed_id = _parse_conversation_id(conversation_id)
    uploads: list[tuple[str, bytes]] = []
    for upload in files:
        if not upload.filename:
            raise AppError(422, "Arquivo sem nome.")
        data = await upload.read()
        uploads.append((upload.filename, data))
    draft_title = uploads[0][0] if uploads else None
    async with request.app.state.sessionmaker() as session:
        conversation = await ensure_conversation_for_upload(
            session,
            settings,
            parsed_id,
            draft_title=draft_title,
        )
        rows = await create_pending_attachments(session, settings, conversation, uploads)
        await session.commit()
    return UploadAttachmentsResponse(
        conversation_id=conversation.id,
        attachments=[
            AttachmentUploaded(id=row.id, filename=row.filename, char_count=len(row.text_content))
            for row in rows
        ],
    )
