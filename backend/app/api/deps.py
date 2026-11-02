import secrets

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.services.chat import ChatService

bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_llm(request: Request):
    return request.app.state.llm


def get_chat_service(request: Request) -> ChatService:
    return ChatService(
        sessionmaker=request.app.state.sessionmaker,
        llm=request.app.state.llm,
        gate=request.app.state.gate,
        settings=request.app.state.settings,
    )


async def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = None,
) -> None:
    expected = request.app.state.settings.api_key
    if not expected:
        return
    received = credentials.credentials if credentials is not None else ""
    if not received or not secrets.compare_digest(received, expected):
        raise HTTPException(status_code=401, detail="Credencial ausente ou inválida.")
