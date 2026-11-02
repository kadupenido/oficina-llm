import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.config import Settings
from app.errors import AppError, MessageTooLong
from app.services.context import prompt_token_estimate
from app.services.llm import ModelChunk, ToolCall
from app.services.tools.web_search import WEB_SEARCH_TOOL, run_web_search

AgentEventKind = Literal["chunk", "tool_start", "tool_end"]


@dataclass
class AgentToolStart:
    name: str
    query: str


@dataclass
class AgentToolEnd:
    sources: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None


@dataclass
class AgentEvent:
    kind: AgentEventKind
    chunk: ModelChunk | None = None
    tool_start: AgentToolStart | None = None
    tool_end: AgentToolEnd | None = None


def _parse_query(arguments: str) -> str:
    text = (arguments or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict):
        query = payload.get("query")
        if isinstance(query, str):
            return query.strip()
    return text


def _assistant_tool_message(call: ToolCall, content: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
        ],
    }


def _tool_result_message(call: ToolCall, content: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "content": content,
    }


def _enforce_context_budget(
    working: list[dict[str, Any]],
    settings: Settings,
    max_tokens: int,
) -> None:
    budget = settings.llama_ctx_size - max_tokens - settings.context_reserve_tokens
    if budget <= 0:
        raise MessageTooLong()
    used = prompt_token_estimate(
        working,
        settings.chars_per_token,
        settings.message_overhead_tokens,
    )
    if used > budget:
        raise MessageTooLong()


async def _stream_answer(
    llm,
    working: list[dict[str, Any]],
    *,
    temperature: float | None,
    max_tokens: int,
    tools: list[dict] | None,
) -> AsyncIterator[AgentEvent]:
    async for chunk in llm.stream(
        working,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tools,
    ):
        yield AgentEvent(kind="chunk", chunk=chunk)


async def stream_with_tools(
    llm,
    settings: Settings,
    messages: list[dict[str, Any]],
    *,
    temperature: float | None,
    max_tokens: int,
    should_abort: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[AgentEvent]:
    if not settings.web_search_enabled:
        async for event in _stream_answer(
            llm,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=None,
        ):
            yield event
        return

    working = list(messages)
    tools = [WEB_SEARCH_TOOL]

    for _round in range(settings.agent_max_tool_rounds):
        assistant_content: list[str] = []
        async for chunk in llm.stream(
            working,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        ):
            if chunk.finish_reason == "tool_calls" and chunk.tool_calls:
                call = chunk.tool_calls[0]
                if call.name != "web_search":
                    raise AppError(502, f"Ferramenta não suportada: {call.name}.")
                query = _parse_query(call.arguments)
                yield AgentEvent(
                    kind="tool_start",
                    tool_start=AgentToolStart(name=call.name, query=query or call.arguments),
                )
                if should_abort is not None and await should_abort():
                    return
                content, sources = await run_web_search(query, settings)
                error = None
                if content.startswith('{"error"'):
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict) and parsed.get("error"):
                            error = str(parsed["error"])
                    except json.JSONDecodeError:
                        pass
                yield AgentEvent(
                    kind="tool_end",
                    tool_end=AgentToolEnd(sources=sources, error=error),
                )
                working.append(_assistant_tool_message(call, "".join(assistant_content)))
                working.append(_tool_result_message(call, content))
                _enforce_context_budget(working, settings, max_tokens)
                async for event in _stream_answer(
                    llm,
                    working,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                ):
                    yield event
                return

            if chunk.content:
                assistant_content.append(chunk.content)
            yield AgentEvent(kind="chunk", chunk=chunk)
            if chunk.finish_reason:
                return

    raise AppError(502, "Limite de chamadas de ferramenta atingido neste turno.")


async def complete_with_tools(
    llm,
    settings: Settings,
    messages: list[dict[str, Any]],
    *,
    temperature: float | None,
    max_tokens: int,
) -> tuple[str, str | None, int | None, int | None]:
    parts: list[str] = []
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    async for event in stream_with_tools(
        llm,
        settings,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
    ):
        if event.kind != "chunk" or event.chunk is None:
            continue
        chunk = event.chunk
        if chunk.content:
            parts.append(chunk.content)
        if chunk.finish_reason:
            finish_reason = chunk.finish_reason
        if chunk.prompt_tokens is not None:
            prompt_tokens = chunk.prompt_tokens
        if chunk.completion_tokens is not None:
            completion_tokens = chunk.completion_tokens
    return "".join(parts), finish_reason, prompt_tokens, completion_tokens
