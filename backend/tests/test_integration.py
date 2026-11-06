import os

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import parse_sse


@pytest.mark.integration
async def test_real_upstream_round_trip(tmp_path):
    base_url = os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:9080/v1")
    api_key = os.environ.get("LLAMA_API_KEY", "").strip()
    if not api_key:
        pytest.skip("defina LLAMA_API_KEY para o teste de integração")
    health_url = base_url.rstrip("/")
    if health_url.endswith("/v1"):
        health_url = health_url[:-3]
    health_url += "/health"
    try:
        response = httpx.get(health_url, timeout=3)
        response.raise_for_status()
    except httpx.HTTPError:
        pytest.skip("serviço de inferência (upstream) não está acessível")

    database = (tmp_path / "integration.db").as_posix()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{database}",
        llama_base_url=base_url,
        llama_api_key=api_key,
        llama_enable_thinking=False,
        llama_ctx_size=int(os.environ.get("LLAMA_CTX_SIZE", "8192")),
        max_tokens_default=32,
        log_level="WARNING",
        api_key="",
    )
    app = create_app(settings)
    from httpx import ASGITransport, AsyncClient

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", timeout=180) as client:
            response = await client.post(
                "/api/chat/stream",
                json={"message": "Responda apenas com a palavra ok.", "max_tokens": 32},
            )
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[0][0] == "meta"
    assert events[-1][0] == "done"
    assert events[-1][1]["content"].strip()
