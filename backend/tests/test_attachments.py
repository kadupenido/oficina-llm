from io import BytesIO

import pytest
from pypdf import PdfWriter

from app.config import Settings
from app.errors import AppError
from app.services.attachments import (
    compose_user_content,
    extract_text,
    is_allowed_filename,
)


def test_is_allowed_filename_rejects_unknown_extension():
    assert not is_allowed_filename("virus.exe")


def test_extract_text_plain():
    settings = Settings(_env_file=None, file_upload_max_bytes=1000, file_upload_max_chars=500)
    text = extract_text("notas.txt", b"ola mundo", settings)
    assert text == "ola mundo"


def test_compose_user_content_with_attachment():
    from app.services.attachments import AttachmentRecord

    composed = compose_user_content(
        "Resuma",
        [AttachmentRecord(id="1", filename="a.txt", text_content="corpo")],
    )
    assert "Resuma" in composed
    assert "Anexo: a.txt" in composed
    assert "corpo" in composed


def test_extract_text_rejects_empty_pdf():
    settings = Settings(_env_file=None, file_upload_max_bytes=100_000, file_upload_max_chars=100_000)
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    with pytest.raises(AppError) as exc:
        extract_text("vazio.pdf", buffer.getvalue(), settings)
    assert exc.value.status_code == 422
