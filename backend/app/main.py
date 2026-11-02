import time
from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api.routes import attachments, chat, conversations, health
from app.config import Settings
from app.db.session import create_engine, ensure_sqlite_dir, init_db, session_factory
from app.errors import AppError
from app.logging import configure_logging
from app.schemas.chat import StreamDone, StreamError, StreamMeta, StreamTitle, StreamToken
from app.services.chat import GenerationGate
from app.services.llm import LLMClient

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    ensure_sqlite_dir(settings.database_url)
    engine = create_engine(settings.database_url)
    app.state.engine = engine
    app.state.sessionmaker = session_factory(engine)
    await init_db(engine)
    if getattr(app.state, "llm", None) is None:
        app.state.llm = LLMClient(settings)
    if getattr(app.state, "gate", None) is None:
        app.state.gate = GenerationGate(settings.llama_parallel)
    try:
        yield
    finally:
        closer = getattr(app.state.llm, "aclose", None)
        if closer is not None:
            await closer()
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="Chat local",
        version="1.0.0",
        summary="API de conversas com um modelo servido pelo llama-server.",
        description=(
            "O navegador fala só com esta API. A inferência fica no llama-server, "
            "compatível com o formato da OpenAI, apontado por LLAMA_BASE_URL."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request_failed", method=request.method, path=request.url.path)
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        log.warning("app_error", status=exc.status_code, detail=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.include_router(health.router)
    app.include_router(chat.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(attachments.router, prefix="/api")
    _install_openapi(app)
    return app


def _install_openapi(app: FastAPI) -> None:
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for model in (StreamMeta, StreamToken, StreamDone, StreamTitle, StreamError):
            produced = model.model_json_schema(ref_template="#/components/schemas/{model}")
            defs = produced.pop("$defs", {})
            components.update(defs)
            components[model.__name__] = produced
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi


app = create_app()
