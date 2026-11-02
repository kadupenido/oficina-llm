from types import SimpleNamespace

import httpx
from openai import APIStatusError

from app.config import Settings
from app.errors import LLMUnavailable, MessageTooLong
from app.services.llm import LLMClient, _delta_parts, _is_context_overflow


def _settings() -> Settings:
    return Settings(_env_file=None, llama_base_url="http://127.0.0.1:9/v1", llama_api_key="local")


def _status(code: int, text: str) -> APIStatusError:
    request = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
    response = httpx.Response(code, request=request, text=text)
    return APIStatusError("falha", response=response, body=text)


async def test_context_overflow_400_becomes_413():
    client = LLMClient(_settings())
    try:
        mapped = client._translate(
            _status(400, "request exceeds the available context size, n_ctx=8192")
        )
        assert isinstance(mapped, MessageTooLong)
        other = client._translate(_status(400, "invalid grammar"))
        assert isinstance(other, LLMUnavailable)
    finally:
        await client.aclose()


def test_delta_parts_separates_reasoning():
    delta = SimpleNamespace(content="Olá", reasoning_content="segredo", model_extra=None)
    assert _delta_parts(delta) == ("segredo", "Olá")
    only_reasoning = SimpleNamespace(content=None, reasoning_content="pensando", model_extra=None)
    assert _delta_parts(only_reasoning) == ("pensando", "")
    assert _is_context_overflow(_status(400, "n_ctx exceeded"))


class _Chunks:
    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)

    async def close(self):
        return None


class _Completions:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if kwargs.get("stream"):
            usage = SimpleNamespace(prompt_tokens=11, completion_tokens=2)
            reasoning = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(
                            content=None,
                            reasoning_content="pensando",
                            model_extra=None,
                        ),
                    )
                ],
                usage=None,
            )
            visible = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="length",
                        delta=SimpleNamespace(content="Oi", reasoning_content=None, model_extra=None),
                    )
                ],
                usage=usage,
            )
            return _Chunks([reasoning, visible])
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="ok"),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=1),
        )


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class _Models:
    async def list(self):
        return SimpleNamespace(data=[SimpleNamespace(id="qwen")])


class _StubClient:
    def __init__(self):
        self.completions = _Completions()
        self.chat = _Chat(self.completions)
        self.models = _Models()

    async def close(self):
        return None


async def test_stream_forwards_reasoning_separate_from_content():
    client = LLMClient(_settings())
    stub = _StubClient()
    client._client = stub
    try:
        pieces = [piece async for piece in client.stream([{"role": "user", "content": "oi"}], temperature=None, max_tokens=8)]
    finally:
        await client._http.aclose()
    assert stub.completions.kwargs["stream_options"] == {"include_usage": True}
    assert [piece.content for piece in pieces] == ["", "Oi"]
    assert [piece.reasoning for piece in pieces] == ["pensando", ""]
    assert pieces[-1].finish_reason == "length"
    assert pieces[-1].prompt_tokens == 11
    assert pieces[-1].completion_tokens == 2


async def test_stream_forwards_usage_that_arrives_without_choices():
    client = LLMClient(_settings())
    text = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                delta=SimpleNamespace(content="Oi", reasoning_content=None, model_extra=None),
            )
        ],
        usage=None,
    )
    only_usage = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
    )

    class _LateUsage:
        def __init__(self):
            self.kwargs = None

        async def create(self, **kwargs):
            self.kwargs = kwargs
            return _Chunks([text, only_usage])

    stub = _StubClient()
    stub.completions = _LateUsage()
    stub.chat = _Chat(stub.completions)
    client._client = stub
    try:
        pieces = [piece async for piece in client.stream([{"role": "user", "content": "oi"}], temperature=None, max_tokens=8)]
    finally:
        await client._http.aclose()
    assert [piece.content for piece in pieces] == ["Oi", ""]
    assert pieces[-1].prompt_tokens == 7
    assert pieces[-1].completion_tokens == 3
