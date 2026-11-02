import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.services.llm import LlamaHealth, ModelChunk, ModelReply, ToolCall


class FakeLLM:
    def __init__(self) -> None:
        self.reply = "Olá mundo"
        self.title = "Titulo curto"
        self.chunks = ["Olá", " mundo"]
        self.chunk_reasoning: list[str] | None = None
        self.fail = False
        self.fail_stream = False
        self.title_fails = False
        self.block = False
        self.health = LlamaHealth(reachable=True, status="ok")
        self.complete_calls: list[dict] = []
        self.stream_calls: list[list[dict]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finish_reason = "stop"
        self.pause_after_chunks = 0
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.tool_call_once = False
        self.tool_call_done = False

    async def aclose(self) -> None:
        return None

    async def check_health(self) -> LlamaHealth:
        return self.health

    async def complete(
        self,
        messages: list[dict],
        *,
        temperature: float | None,
        max_tokens: int,
        timeout: float | None = None,
        tools: list | None = None,
    ) -> ModelReply:
        self.complete_calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "tools": tools,
            }
        )
        system = messages[0]["content"] if messages else ""
        if "título" in system.lower():
            if self.title_fails:
                from app.errors import LLMUnavailable

                raise LLMUnavailable("title")
            return ModelReply(content=self.title, finish_reason=self.finish_reason)
        if tools and self.tool_call_once and not self.tool_call_done:
            self.tool_call_done = True
            return ModelReply(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call_test",
                        name="web_search",
                        arguments='{"query":"clima hoje"}',
                    )
                ],
            )
        if self.fail:
            from app.errors import LLMUnavailable

            raise LLMUnavailable("down")
        return ModelReply(content=self.reply, finish_reason=self.finish_reason)

    async def stream(
        self,
        messages: list[dict],
        *,
        temperature: float | None,
        max_tokens: int,
        tools: list | None = None,
    ) -> AsyncIterator[ModelChunk]:
        self.stream_calls.append({"messages": messages, "tools": tools})
        if self.fail_stream:
            from app.errors import LLMUnavailable

            raise LLMUnavailable("stream down")
        self.started.set()
        if self.block:
            await self.release.wait()
        if tools and self.tool_call_once and not self.tool_call_done:
            self.tool_call_done = True
            yield ModelChunk(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call_test",
                        name="web_search",
                        arguments='{"query":"clima hoje"}',
                    )
                ],
            )
            return
        for index, chunk in enumerate(self.chunks):
            if self.pause_after_chunks and index == self.pause_after_chunks:
                await self.release.wait()
            last = index == len(self.chunks) - 1
            finish = self.finish_reason if last else None
            reasoning = ""
            if self.chunk_reasoning is not None and index < len(self.chunk_reasoning):
                reasoning = self.chunk_reasoning[index]
            yield ModelChunk(
                content=chunk,
                reasoning=reasoning,
                finish_reason=finish,
                prompt_tokens=self.prompt_tokens if last else None,
                completion_tokens=self.completion_tokens if last else None,
            )


@asynccontextmanager
async def make_client(tmp_path: Path, **overrides):
    database = (tmp_path / "test.db").as_posix()
    values = {
        "database_url": f"sqlite+aiosqlite:///{database}",
        "api_key": "",
        "llama_base_url": "http://127.0.0.1:9/v1",
        "llama_parallel": 1,
        "llama_ctx_size": 8192,
        "max_tokens_default": 128,
        "context_reserve_tokens": 32,
        "chars_per_token": 4,
        "system_prompt": "SYS",
        "llama_enable_thinking": False,
        "llama_timeout_seconds": 30,
        "cors_origins": "http://localhost:5173,http://127.0.0.1:5173",
        "log_level": "WARNING",
    }
    values.update(overrides)
    settings = Settings(_env_file=None, **values)
    app = create_app(settings)
    fake = FakeLLM()
    app.state.llm = fake
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, fake, app


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            events.append((event, json.loads("\n".join(data_lines))))
    return events


@pytest.fixture
def anyio_backend():
    return "asyncio"
