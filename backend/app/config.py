from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

WEB_SEARCH_SYSTEM_APPEND = (
    " Quando precisar de informação recente ou factos que não estão no histórico, "
    "use a ferramenta web_search. Cite as URLs relevantes na resposta. "
    "Se a busca falhar ou não trouxer resultados, diga isso em vez de inventar."
)


def _env_files() -> tuple[str, ...]:
    root = Path(__file__).resolve().parents[2]
    found: list[str] = []
    for candidate in (root / ".env", root / "backend" / ".env"):
        if candidate.is_file():
            found.append(str(candidate))
    return tuple(found)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_files() or None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llama_base_url: str = "http://127.0.0.1:8080/v1"
    llama_api_key: str = "local"
    llama_model: str = ""
    llama_timeout_seconds: float = 300
    llama_enable_thinking: bool = False
    llama_ctx_size: int = 8192
    llama_parallel: int = 1
    max_tokens_default: int = 2048
    message_overhead_tokens: int = 4
    sse_ping_seconds: float = 15
    context_reserve_tokens: int = 256
    chars_per_token: float = 2.5
    system_prompt: str = (
        "Você é um assistente útil. Responda no idioma do usuário, de forma clara e direta."
    )
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    api_key: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"
    title_max_chars: int = 48
    title_max_tokens: int = 24
    web_search_enabled: bool = False
    tavily_api_key: str = ""
    web_search_max_results: int = 5
    web_search_max_chars: int = 12000
    agent_max_tool_rounds: int = 3
    file_upload_enabled: bool = True
    file_upload_max_bytes: int = 5_000_000
    file_upload_max_files: int = 5
    file_upload_max_chars: int = 100_000

    @model_validator(mode="after")
    def validate_web_search(self) -> "Settings":
        if self.web_search_enabled and not self.tavily_api_key.strip():
            raise ValueError(
                "TAVILY_API_KEY é obrigatória quando WEB_SEARCH_ENABLED=true."
            )
        return self

    def effective_system_prompt(self, base: str) -> str:
        if not self.web_search_enabled:
            return base
        return base.rstrip() + WEB_SEARCH_SYSTEM_APPEND

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def llama_health_url(self) -> str:
        root = self.llama_base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        return f"{root}/health"
