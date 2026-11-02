from pydantic import BaseModel, ConfigDict, Field


class AttachmentSummary(BaseModel):
    id: str
    filename: str

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "filename": "notas.md"}]
        }
    )


class AttachmentUploaded(BaseModel):
    id: str
    filename: str
    char_count: int


class UploadAttachmentsResponse(BaseModel):
    conversation_id: str
    attachments: list[AttachmentUploaded] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "conversation_id": "11111111-2222-4333-8444-555555555555",
                    "attachments": [
                        {
                            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            "filename": "notas.md",
                            "char_count": 120,
                        }
                    ],
                }
            ]
        }
    )
