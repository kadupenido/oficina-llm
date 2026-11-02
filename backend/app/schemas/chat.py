from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from app.schemas.attachments import AttachmentSummary


def _as_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class ChatRequest(BaseModel):
    conversation_id: str | None = Field(
        default=None,
        description="Conversa existente. Vazio cria uma conversa nova.",
    )
    message: str = Field(default="", max_length=32000)
    attachment_ids: list[str] = Field(
        default_factory=list,
        description="Anexos já enviados com POST /api/attachments, ainda sem message_id.",
    )
    regenerate: bool = Field(
        default=False,
        description=(
            "Reusa a última mensagem do usuário e não grava outra. "
            "O texto de message é ignorado."
        ),
    )
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "message": "Explique o que é um índice em uma frase.",
                    "temperature": 1.0,
                    "max_tokens": 256,
                }
            ]
        }
    )

    @field_validator("conversation_id")
    @classmethod
    def validate_conversation_id(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise ValueError("conversation_id deve ser um UUID.") from exc

    @field_validator("attachment_ids")
    @classmethod
    def validate_attachment_ids(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            try:
                parsed = str(UUID(item))
            except ValueError as exc:
                raise ValueError("attachment_ids deve conter UUIDs válidos.") from exc
            if parsed in seen:
                continue
            seen.add(parsed)
            normalized.append(parsed)
        return normalized

    @model_validator(mode="after")
    def message_or_attachments(self) -> "ChatRequest":
        if self.regenerate:
            return self
        if not self.message.strip() and not self.attachment_ids:
            raise ValueError("Informe uma mensagem ou anexe pelo menos um arquivo.")
        return self


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    status: str
    created_at: datetime
    attachments: list[AttachmentSummary] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "3f1c2a4e-6b7d-4e8f-9a10-223344556677",
                    "role": "assistant",
                    "content": "Um índice acelera a busca em troca de espaço e escrita.",
                    "status": "complete",
                    "created_at": "2026-10-31T13:00:00+00:00",
                }
            ]
        }
    )

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return _as_utc(value)


class ChatResponse(BaseModel):
    conversation_id: str
    title: str
    history_truncated: bool
    finish_reason: str | None = None
    message: MessageOut

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "conversation_id": "11111111-2222-4333-8444-555555555555",
                    "title": "O que é um índice",
                    "history_truncated": False,
                    "finish_reason": "stop",
                    "message": {
                        "id": "3f1c2a4e-6b7d-4e8f-9a10-223344556677",
                        "role": "assistant",
                        "content": "Um índice acelera a busca em troca de espaço e escrita.",
                        "status": "complete",
                        "created_at": "2026-10-31T13:00:00+00:00",
                    },
                }
            ]
        }
    )


class StreamMeta(BaseModel):
    conversation_id: str
    user_message_id: str
    title: str
    context_tokens: int
    context_limit: int


class StreamToken(BaseModel):
    delta: str


class StreamThink(BaseModel):
    delta: str


class StreamToolSource(BaseModel):
    title: str
    url: str


class StreamToolStart(BaseModel):
    name: str
    query: str


class StreamToolEnd(BaseModel):
    sources: list[StreamToolSource] = Field(default_factory=list)
    error: str | None = None


class StreamDone(BaseModel):
    message_id: str
    content: str
    status: str
    title: str
    history_truncated: bool
    finish_reason: str | None = None
    context_tokens: int
    tokens_per_second: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    thinking: str | None = None


class StreamTitle(BaseModel):
    conversation_id: str
    title: str


class StreamError(BaseModel):
    detail: str


class ErrorBody(BaseModel):
    detail: str

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"detail": "Conversa não encontrada."}]}
    )
