import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from app.config import Settings
from app.errors import LLMTimeout, LLMUnavailable, MessageTooLong

log = structlog.get_logger(__name__)


def _delta_parts(delta) -> tuple[str, str]:
    """Separa raciocínio e texto visível de um delta de stream."""
    if delta is None:
        return "", ""
    content = delta.content or ""
    reasoning = getattr(delta, "reasoning_content", None)
    extra = getattr(delta, "model_extra", None)
    if reasoning is None and isinstance(extra, dict):
        reasoning = extra.get("reasoning_content")
    if reasoning:
        return str(reasoning), content
    return "", content


def _is_context_overflow(exc: APIStatusError) -> bool:
    if exc.status_code != 400:
        return False
    try:
        body = exc.response.text or ""
    except Exception:
        body = ""
    text = f"{body}\n{exc}".lower()
    if "context" not in text and "n_ctx" not in text:
        return False
    return any(token in text for token in ("exceed", "too many", "too long", "n_ctx", "overflow"))


@dataclass
class LlamaHealth:
    reachable: bool
    status: str


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ModelReply:
    content: str
    finish_reason: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ModelChunk:
    content: str = ""
    reasoning: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


def _usage_pair(usage) -> tuple[int | None, int | None]:
    if usage is None:
        return None, None
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
    else:
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
    if prompt_tokens is None and completion_tokens is None:
        return None, None
    return prompt_tokens, completion_tokens


def _parse_tool_calls(message) -> list[ToolCall]:
    raw = getattr(message, "tool_calls", None) or []
    parsed: list[ToolCall] = []
    for item in raw:
        fn = getattr(item, "function", None)
        if fn is None and isinstance(item, dict):
            fn = item.get("function")
        name = getattr(fn, "name", None) if fn is not None else None
        args = getattr(fn, "arguments", None) if fn is not None else None
        call_id = getattr(item, "id", None)
        if name is None and isinstance(item, dict):
            fn_dict = item.get("function") or {}
            name = fn_dict.get("name")
            args = fn_dict.get("arguments")
            call_id = item.get("id")
        if not name:
            continue
        parsed.append(
            ToolCall(
                id=str(call_id or ""),
                name=str(name),
                arguments=str(args or ""),
            )
        )
    return parsed


def _accumulate_tool_deltas(
    pending: dict[int, dict[str, str]],
    delta_tool_calls,
) -> None:
    if not delta_tool_calls:
        return
    for item in delta_tool_calls:
        index = getattr(item, "index", None)
        if index is None and isinstance(item, dict):
            index = item.get("index", 0)
        index = int(index or 0)
        slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
        call_id = getattr(item, "id", None)
        if call_id is None and isinstance(item, dict):
            call_id = item.get("id")
        if call_id:
            slot["id"] = str(call_id)
        fn = getattr(item, "function", None)
        if fn is None and isinstance(item, dict):
            fn = item.get("function")
        if fn is not None:
            name = getattr(fn, "name", None)
            args = getattr(fn, "arguments", None)
            if name is None and isinstance(fn, dict):
                name = fn.get("name")
                args = fn.get("arguments")
            if name:
                slot["name"] = str(name)
            if args:
                slot["arguments"] += str(args)


def _pending_to_tool_calls(pending: dict[int, dict[str, str]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index in sorted(pending):
        slot = pending[index]
        if not slot.get("name"):
            continue
        calls.append(
            ToolCall(
                id=slot.get("id") or f"call_{index}",
                name=slot["name"],
                arguments=slot.get("arguments") or "",
            )
        )
    return calls


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = settings.llama_model or None
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0))
        self._open_streams: set = set()
        self._client = self._new_client()

    def _new_client(self) -> AsyncOpenAI:
        timeout = httpx.Timeout(self._settings.llama_timeout_seconds, connect=10.0)
        return AsyncOpenAI(
            base_url=self._settings.llama_base_url,
            api_key=self._settings.llama_api_key or "local",
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.close()
        await self._http.aclose()

    async def check_health(self) -> LlamaHealth:
        try:
            response = await self._http.get(self._settings.llama_health_url)
        except httpx.HTTPError:
            return LlamaHealth(reachable=False, status="unreachable")
        if response.status_code >= 400:
            return LlamaHealth(reachable=False, status=f"http_{response.status_code}")
        status = "ok"
        try:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("status"):
                status = str(payload["status"])
        except ValueError:
            status = "ok"
        return LlamaHealth(reachable=True, status=status)

    async def resolve_model(self) -> str:
        if self._model:
            return self._model
        models = await self._client.models.list()
        if not models.data:
            raise LLMUnavailable("O servidor não reportou nenhum modelo.")
        self._model = models.data[0].id
        return self._model

    async def _safe_model(self) -> str:
        try:
            return await self.resolve_model()
        except Exception as exc:
            raise self._translate(exc) from exc

    def _sampling(self, temperature: float | None, max_tokens: int) -> dict:
        payload: dict = {"max_tokens": max_tokens}
        if temperature is not None:
            payload["temperature"] = temperature
        if not self._settings.llama_enable_thinking:
            payload["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        return payload

    def _tool_payload(self, tools: list[dict] | None) -> dict:
        if not tools:
            return {}
        return {"tools": tools, "tool_choice": "auto"}

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None,
        max_tokens: int,
        timeout: float | None = None,
        tools: list[dict] | None = None,
    ) -> ModelReply:
        request: dict = {
            "model": await self._safe_model(),
            "messages": messages,
            "stream": False,
            **self._sampling(temperature, max_tokens),
            **self._tool_payload(tools),
        }
        if timeout is not None:
            request["timeout"] = timeout
        try:
            response = await self._client.chat.completions.create(**request)
        except Exception as exc:
            raise self._translate(exc) from exc
        prompt_tokens, completion_tokens = _usage_pair(getattr(response, "usage", None))
        if prompt_tokens is not None or completion_tokens is not None:
            log.info(
                "llm_usage",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        choice = response.choices[0]
        message = choice.message
        return ModelReply(
            content=message.content or "",
            finish_reason=choice.finish_reason,
            tool_calls=_parse_tool_calls(message),
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None,
        max_tokens: int,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[ModelChunk]:
        try:
            model = await self.resolve_model()
            chunks = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                **self._sampling(temperature, max_tokens),
                **self._tool_payload(tools),
            )
        except Exception as exc:
            raise self._translate(exc) from exc
        self._open_streams.add(chunks)
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        sent_usage = False
        pending_tools: dict[int, dict[str, str]] = {}
        try:
            async for chunk in chunks:
                got_prompt, got_completion = _usage_pair(getattr(chunk, "usage", None))
                if got_prompt is not None or got_completion is not None:
                    if got_prompt is not None:
                        prompt_tokens = got_prompt
                    if got_completion is not None:
                        completion_tokens = got_completion
                    log.info(
                        "llm_usage",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                _accumulate_tool_deltas(pending_tools, getattr(delta, "tool_calls", None))
                reasoning, content = _delta_parts(delta)
                reason = choice.finish_reason
                tool_calls = _pending_to_tool_calls(pending_tools) if reason == "tool_calls" else []
                if not reasoning and not content and not reason and not tool_calls:
                    continue
                carries_usage = prompt_tokens is not None or completion_tokens is not None
                yield ModelChunk(
                    content=content,
                    reasoning=reasoning,
                    finish_reason=reason,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    tool_calls=tool_calls,
                )
                if carries_usage:
                    sent_usage = True
            if (prompt_tokens is not None or completion_tokens is not None) and not sent_usage:
                yield ModelChunk(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        except asyncio.CancelledError:
            await self._close_stream(chunks)
            raise
        except Exception as exc:
            raise self._translate(exc) from exc
        finally:
            self._open_streams.discard(chunks)
            await self._close_stream(chunks)

    async def abort_streams(self) -> None:
        """Derruba o TCP do modelo. Fechar só o iterador não interrompe o slot."""
        self._open_streams.clear()
        await self._client.close()
        self._client = self._new_client()

    async def _close_stream(self, chunks) -> None:
        close = getattr(chunks, "close", None)
        if close is None:
            return
        try:
            await asyncio.shield(close())
        except Exception:
            log.warning("llm_stream_close_failed")

    def _translate(self, exc: Exception) -> Exception:
        if isinstance(exc, (LLMUnavailable, LLMTimeout)):
            return exc
        if isinstance(exc, (APITimeoutError, asyncio.TimeoutError)):
            log.warning("llm_timeout", error=str(exc))
            return LLMTimeout()
        if isinstance(exc, APIConnectionError):
            log.warning("llm_unreachable", error=str(exc))
            return LLMUnavailable()
        if isinstance(exc, APIStatusError):
            log.warning("llm_status", status=exc.status_code)
            if _is_context_overflow(exc):
                return MessageTooLong()
            if exc.status_code == 504:
                return LLMTimeout()
            return LLMUnavailable()
        log.exception("llm_unexpected")
        return LLMUnavailable("Falha ao consultar o modelo.")
