from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import PurePath
from uuid import uuid4

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Conversation, MessageAttachment
from app.errors import AppError
from app.services.titles import provisional_title


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

ALLOWED_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".csv",
        ".tsv",
        ".xml",
        ".html",
        ".htm",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".css",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".log",
        ".rs",
        ".go",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".sql",
        ".sh",
        ".bat",
        ".ps1",
        ".pdf",
    }
)


@dataclass(frozen=True)
class AttachmentRecord:
    id: str
    filename: str
    text_content: str


def extension_of(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def is_allowed_filename(filename: str) -> bool:
    name = (filename or "").strip()
    if not name or name.endswith("/") or ".." in PurePath(name).parts:
        return False
    return extension_of(name) in ALLOWED_EXTENSIONS


def decode_text_bytes(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def extract_pdf_text(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    return "\n".join(parts).strip()


def extract_text(filename: str, data: bytes, settings: Settings) -> str:
    if not is_allowed_filename(filename):
        raise AppError(422, "Tipo de arquivo não permitido.")
    if len(data) > settings.file_upload_max_bytes:
        raise AppError(
            413,
            f"O arquivo excede o limite de {settings.file_upload_max_bytes} bytes.",
        )
    ext = extension_of(filename)
    if ext == ".pdf":
        text = extract_pdf_text(data)
    else:
        text = decode_text_bytes(data).strip()
    if not text:
        raise AppError(422, "Não foi possível extrair texto do arquivo.")
    if len(text) > settings.file_upload_max_chars:
        raise AppError(
            413,
            f"O texto extraído excede o limite de {settings.file_upload_max_chars} caracteres.",
        )
    return text


def compose_user_content(user_text: str, attachments: list[AttachmentRecord]) -> str:
    body = user_text.strip()
    if not attachments:
        return body
    blocks: list[str] = []
    if body:
        blocks.append(body)
    for item in attachments:
        blocks.append(f"---\nAnexo: {item.filename}\n{item.text_content}\n---")
    return "\n\n".join(blocks)


async def ensure_conversation_for_upload(
    session: AsyncSession,
    settings: Settings,
    conversation_id: str | None,
    *,
    draft_title: str | None = None,
) -> Conversation:
    if conversation_id:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise AppError(404, "Conversa não encontrada.")
        return conversation
    title_source = (draft_title or "").strip() or "Nova conversa"
    now = utcnow()
    conversation = Conversation(
        id=str(uuid4()),
        title=provisional_title(title_source, settings.title_max_chars),
        system_prompt=settings.system_prompt,
        created_at=now,
        updated_at=now,
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def create_pending_attachments(
    session: AsyncSession,
    settings: Settings,
    conversation: Conversation,
    uploads: list[tuple[str, bytes]],
) -> list[MessageAttachment]:
    if not settings.file_upload_enabled:
        raise AppError(503, "Upload de arquivos desativado.")
    if not uploads:
        raise AppError(422, "Nenhum arquivo enviado.")
    if len(uploads) > settings.file_upload_max_files:
        raise AppError(
            422,
            f"No máximo {settings.file_upload_max_files} arquivos por envio.",
        )
    now = utcnow()
    rows: list[MessageAttachment] = []
    for filename, data in uploads:
        text = extract_text(filename, data, settings)
        row = MessageAttachment(
            id=str(uuid4()),
            conversation_id=conversation.id,
            message_id=None,
            filename=PurePath(filename).name,
            text_content=text,
            created_at=now,
        )
        session.add(row)
        rows.append(row)
    conversation.updated_at = now
    await session.flush()
    return rows


async def link_attachments(
    session: AsyncSession,
    *,
    conversation_id: str,
    message_id: str,
    attachment_ids: list[str],
) -> list[AttachmentRecord]:
    if not attachment_ids:
        return []
    rows = (
        await session.scalars(
            select(MessageAttachment).where(MessageAttachment.id.in_(attachment_ids))
        )
    ).all()
    found = {row.id: row for row in rows}
    missing = [item for item in attachment_ids if item not in found]
    if missing:
        raise AppError(404, "Anexo não encontrado.")
    linked: list[AttachmentRecord] = []
    for attachment_id in attachment_ids:
        row = found[attachment_id]
        if row.conversation_id != conversation_id:
            raise AppError(422, "Anexo pertence a outra conversa.")
        if row.message_id is not None and row.message_id != message_id:
            raise AppError(422, "Anexo já foi usado em outra mensagem.")
        row.message_id = message_id
        linked.append(
            AttachmentRecord(id=row.id, filename=row.filename, text_content=row.text_content)
        )
    return linked


async def load_attachments_for_messages(
    session: AsyncSession,
    message_ids: list[str],
) -> dict[str, list[AttachmentRecord]]:
    if not message_ids:
        return {}
    rows = (
        await session.scalars(
            select(MessageAttachment)
            .where(MessageAttachment.message_id.in_(message_ids))
            .order_by(MessageAttachment.created_at.asc(), MessageAttachment.id.asc())
        )
    ).all()
    grouped: dict[str, list[AttachmentRecord]] = {}
    for row in rows:
        if row.message_id is None:
            continue
        grouped.setdefault(row.message_id, []).append(
            AttachmentRecord(id=row.id, filename=row.filename, text_content=row.text_content)
        )
    return grouped


async def fetch_pending_attachments(
    session: AsyncSession,
    conversation_id: str,
    attachment_ids: list[str],
) -> list[AttachmentRecord]:
    if not attachment_ids:
        return []
    rows = (
        await session.scalars(
            select(MessageAttachment).where(MessageAttachment.id.in_(attachment_ids))
        )
    ).all()
    found = {row.id: row for row in rows}
    missing = [item for item in attachment_ids if item not in found]
    if missing:
        raise AppError(404, "Anexo não encontrado.")
    records: list[AttachmentRecord] = []
    for attachment_id in attachment_ids:
        row = found[attachment_id]
        if row.conversation_id != conversation_id:
            raise AppError(422, "Anexo pertence a outra conversa.")
        if row.message_id is not None:
            raise AppError(422, "Anexo já foi usado em outra mensagem.")
        records.append(
            AttachmentRecord(id=row.id, filename=row.filename, text_content=row.text_content)
        )
    return records
