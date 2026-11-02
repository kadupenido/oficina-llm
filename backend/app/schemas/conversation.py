from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.schemas.chat import MessageOut, _as_utc


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "11111111-2222-4333-8444-555555555555",
                    "title": "O que é um índice",
                    "created_at": "2026-10-31T13:00:00+00:00",
                    "updated_at": "2026-10-31T13:01:00+00:00",
                }
            ]
        }
    )

    @field_serializer("created_at", "updated_at")
    def serialize_times(self, value: datetime) -> str:
        return _as_utc(value)


class ConversationList(BaseModel):
    conversations: list[ConversationSummary]


class ConversationDetail(ConversationSummary):
    messages: list[MessageOut]
    context_tokens: int
    context_limit: int


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"title": "Pergunta sobre índices"}]}
    )
