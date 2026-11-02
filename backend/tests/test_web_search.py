import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.tools.web_search import run_web_search


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        if not block.strip() or block.strip().startswith(":"):
            continue
        event = "message"
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if data:
            events.append((event, json.loads(data)))
    return events


@pytest.mark.asyncio
async def test_web_search_stream_events(tmp_path):
    from tests.conftest import make_client

    async with make_client(
        tmp_path,
        web_search_enabled=True,
        tavily_api_key="test-key",
    ) as (client, fake, _app):
        fake.tool_call_once = True
        fake.chunks = ["Resposta ", "final"]
        mock_search = AsyncMock(
            return_value=(
                '{"query":"clima hoje","results":[{"title":"Tempo","url":"https://exemplo.test","snippet":"Sol"}]}',
                [{"title": "Tempo", "url": "https://exemplo.test"}],
            )
        )
        with patch("app.services.agent.run_web_search", mock_search):
            response = await client.post("/api/chat/stream", json={"message": "Como está o clima?"})
        assert response.status_code == 200
        events = _parse_sse(response.text)
        kinds = [name for name, _ in events]
        assert "tool_start" in kinds
        assert "tool_end" in kinds
        assert "token" in kinds
        assert "done" in kinds
        tool_start = next(payload for name, payload in events if name == "tool_start")
        assert tool_start["name"] == "web_search"
        mock_search.assert_awaited_once()
        meta = next(payload for name, payload in events if name == "meta")
        detail = await client.get(f"/api/conversations/{meta['conversation_id']}")
        assert detail.status_code == 200
        roles = [item["role"] for item in detail.json()["messages"]]
        assert roles.count("assistant") == 1
        assert "tool" not in roles


@pytest.mark.asyncio
async def test_web_search_direct_answer_streams(tmp_path):
    from tests.conftest import make_client

    async with make_client(
        tmp_path,
        web_search_enabled=True,
        tavily_api_key="test-key",
    ) as (client, fake, _app):
        fake.chunks = ["Olá", " mundo"]
        response = await client.post("/api/chat/stream", json={"message": "oi"})
        assert response.status_code == 200
        events = _parse_sse(response.text)
        tokens = [payload["delta"] for name, payload in events if name == "token"]
        assert tokens == ["Olá", " mundo"]
        assert "tool_start" not in [name for name, _ in events]


@pytest.mark.asyncio
async def test_web_search_disabled_no_tool_events(tmp_path):
    from tests.conftest import make_client

    async with make_client(tmp_path) as (client, _fake, _app):
        response = await client.post("/api/chat/stream", json={"message": "oi"})
        assert response.status_code == 200
        kinds = [name for name, _ in _parse_sse(response.text)]
        assert "tool_start" not in kinds


@pytest.mark.asyncio
async def test_run_web_search_truncates_payload():
    from app.config import Settings

    settings = Settings(
        tavily_api_key="k",
        web_search_max_results=2,
        web_search_max_chars=80,
    )

    async def fake_post(*_args, **_kwargs):
        class Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "results": [
                        {
                            "title": "A" * 40,
                            "url": "https://a.test",
                            "content": "B" * 200,
                        }
                    ]
                }

        return Response()

    client = AsyncMock()
    client.post = fake_post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.tools.web_search.httpx.AsyncClient", return_value=client):
        content, sources = await run_web_search("teste", settings)
    assert len(content) <= 80
    assert sources[0]["url"] == "https://a.test"
