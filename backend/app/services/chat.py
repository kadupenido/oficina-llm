import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Conversation, Message
from app.errors import AppError, ConversationBusy, LLMCapacity, NothingToRegenerate
from app.services.agent import complete_with_tools, stream_with_tools
from app.services.attachments import (
    AttachmentRecord,
    compose_user_content,
    link_attachments,
    load_attachments_for_messages,
)
from app.services.context import build_context, prompt_messages, prompt_token_estimate
from app.services.llm import ModelChunk, ModelReply
from app.services.think import ThinkStripper, strip_think
from app.services.titles import clean_title, provisional_title, title_messages

log = structlog.get_logger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class GenerationGate:
    def __init__(self, parallel: int) -> None:
        self._guard = asyncio.Lock()
        self._conversations: dict[str, asyncio.Lock] = {}
        self._slots = asyncio.Semaphore(max(1, parallel))

    async def acquire_conversation(self, conversation_id: str) -> None:
        async with self._guard:
            lock = self._conversations.setdefault(conversation_id, asyncio.Lock())
            if lock.locked():
                raise ConversationBusy()
            await lock.acquire()

    def release_conversation(self, conversation_id: str) -> None:
        lock = self._conversations.get(conversation_id)
        if lock is not None and lock.locked():
            lock.release()

    async def try_acquire_slot(self) -> bool:
        async with self._guard:
            if self._slots.locked():
                return False
            await self._slots.acquire()
            return True

    def release_slot(self) -> None:
        self._slots.release()


@dataclass
class PreparedTurn:
    conversation_id: str
    user_message_id: str
    title: str
    provisional_title: str
    llm_messages: list[dict[str, str]]
    history_truncated: bool
    context_tokens: int
    context_limit: int
    user_message_count: int
    first_user_content: str
    max_tokens: int
    temperature: float | None
    slot_held: bool = True
    conv_held: bool = True
    inflight: asyncio.Task | None = None
    watcher: asyncio.Task | None = None
    deferred_release: bool = False


@dataclass
class TurnResult:
    conversation_id: str
    title: str
    history_truncated: bool
    message: Message
    finish_reason: str | None = None


class _Ping:
    pass


PING = _Ping()


class ChatService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        llm,
        gate: GenerationGate,
        settings: Settings,
    ) -> None:
        self._sessions = sessionmaker
        self._llm = llm
        self._gate = gate
        self._settings = settings
        self._cleanups: set[asyncio.Task] = set()

    async def prepare(
        self,
        conversation_id: str | None,
        message: str,
        *,
        temperature: float | None,
        max_tokens: int | None,
        regenerate: bool = False,
        attachment_ids: list[str] | None = None,
    ) -> PreparedTurn:
        if regenerate and not conversation_id:
            raise NothingToRegenerate()
        ids = attachment_ids or []
        if ids and not conversation_id:
            raise AppError(422, "Informe conversation_id ao enviar anexos.")
        effective_max = max_tokens or self._settings.max_tokens_default
        if conversation_id is None:
            if ids:
                raise AppError(422, "Informe conversation_id ao enviar anexos.")
            return await self._prepare_new(message, temperature, effective_max)
        return await self._prepare_existing(
            conversation_id,
            message,
            temperature,
            effective_max,
            regenerate=regenerate,
            attachment_ids=ids,
        )

    async def complete(self, turn: PreparedTurn) -> TurnResult:
        try:
            if self._settings.web_search_enabled:
                text, finish, _prompt, _completion = await complete_with_tools(
                    self._llm,
                    self._settings,
                    turn.llm_messages,
                    temperature=turn.temperature,
                    max_tokens=turn.max_tokens,
                )
                reply = ModelReply(content=text, finish_reason=finish)
            else:
                raw = await self._llm.complete(
                    turn.llm_messages,
                    temperature=turn.temperature,
                    max_tokens=turn.max_tokens,
                )
                reply = raw if isinstance(raw, ModelReply) else ModelReply(content=str(raw))
            content = strip_think(reply.content)
            result = await self.persist_assistant(turn, content, "complete")
            title = await self._write_title(turn)
            if title:
                result.title = title
            result.finish_reason = reply.finish_reason
            return result
        except Exception:
            await self.persist_assistant(turn, "", "error")
            raise
        finally:
            self.release(turn)

    async def generate_stream(self, turn: PreparedTurn, request) -> AsyncIterator[tuple[str, dict]]:
        parts: list[str] = []
        think_parts: list[str] = []
        stripper = ThinkStripper()
        expose_thinking = self._settings.llama_enable_thinking
        persisted = False
        status = "complete"
        finish_reason: str | None = None
        started_at: float | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None

        def emit_think(delta: str) -> tuple[str, dict] | None:
            if not delta or not expose_thinking:
                return None
            think_parts.append(delta)
            return "think", {"delta": delta}

        try:
            async for item in self._iter_stream(turn, request):
                if item is PING:
                    yield "ping", {}
                    continue
                if item is None:
                    status = "interrupted"
                    await self.persist_assistant(turn, "".join(parts), status)
                    persisted = True
                    break
                if isinstance(item, tuple):
                    yield item[0], item[1]
                    continue
                if item.prompt_tokens is not None:
                    prompt_tokens = item.prompt_tokens
                if item.completion_tokens is not None:
                    completion_tokens = item.completion_tokens
                if item.finish_reason:
                    finish_reason = item.finish_reason
                think_event = emit_think(item.reasoning)
                if think_event:
                    yield think_event
                if expose_thinking:
                    think_delta, visible = stripper.feed_split(item.content)
                else:
                    think_delta = ""
                    visible = stripper.feed(item.content)
                think_event = emit_think(think_delta)
                if think_event:
                    yield think_event
                if visible:
                    if started_at is None:
                        started_at = time.perf_counter()
                    parts.append(visible)
                    yield "token", {"delta": visible}
            finished_at = time.perf_counter() if started_at is not None else None
            if status == "complete":
                tail = stripper.flush()
                if tail:
                    if started_at is None:
                        started_at = time.perf_counter()
                        finished_at = started_at
                    parts.append(tail)
                    yield "token", {"delta": tail}
            if not persisted:
                result = await self.persist_assistant(turn, "".join(parts), status)
                persisted = True
            if status == "complete":
                measured = turn.context_tokens
                tokens_per_second: float | None = None
                if prompt_tokens is not None and completion_tokens is not None:
                    measured = prompt_tokens + completion_tokens
                    if started_at is not None and finished_at is not None and finished_at > started_at:
                        tokens_per_second = completion_tokens / (finished_at - started_at)
                thinking_text = "".join(think_parts) if expose_thinking and think_parts else None
                yield "done", {
                    "message_id": result.message.id,
                    "content": result.message.content,
                    "status": result.message.status,
                    "title": result.title,
                    "history_truncated": result.history_truncated,
                    "finish_reason": finish_reason,
                    "context_tokens": measured,
                    "tokens_per_second": tokens_per_second,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "thinking": thinking_text,
                }
                self.release(turn)
                title = await self._title_if_slot_free(turn)
                if title:
                    yield "title", {"conversation_id": turn.conversation_id, "title": title}
        except asyncio.CancelledError:
            if not persisted:
                self._schedule_cancel_cleanup(turn, "".join(parts))
            raise
        except AppError as exc:
            if not persisted:
                await self.persist_assistant(turn, "".join(parts), "error")
            yield "error", {"detail": exc.detail}
        except Exception:
            log.exception("stream_failed", conversation_id=turn.conversation_id)
            if not persisted:
                await self.persist_assistant(turn, "".join(parts), "error")
            yield "error", {"detail": "Falha ao gerar a resposta."}

    async def _message_iterator(self, turn: PreparedTurn, request):
        if not self._settings.web_search_enabled:
            async for chunk in self._llm.stream(
                turn.llm_messages,
                temperature=turn.temperature,
                max_tokens=turn.max_tokens,
            ):
                yield chunk
            return

        async def should_abort() -> bool:
            return self._polls_disconnect(request) and await request.is_disconnected()

        async for event in stream_with_tools(
            self._llm,
            self._settings,
            turn.llm_messages,
            temperature=turn.temperature,
            max_tokens=turn.max_tokens,
            should_abort=should_abort,
        ):
            if event.kind == "tool_start" and event.tool_start is not None:
                yield (
                    "tool_start",
                    {
                        "name": event.tool_start.name,
                        "query": event.tool_start.query,
                    },
                )
            elif event.kind == "tool_end" and event.tool_end is not None:
                payload: dict = {"sources": event.tool_end.sources}
                if event.tool_end.error:
                    payload["error"] = event.tool_end.error
                yield ("tool_end", payload)
            elif event.kind == "chunk" and event.chunk is not None:
                yield event.chunk

    async def _iter_stream(self, turn: PreparedTurn, request):
        """Emite ModelChunk, tuplas de evento tool, PING ou None.

        None significa que o cliente desconectou, inclusive durante o prompt.
        PING é um comentário SSE para o proxy não idle-timeout enquanto o modelo processa.
        """
        iterator = self._message_iterator(turn, request).__aiter__()
        pending: asyncio.Task | None = None
        watcher: asyncio.Task | None = None
        try:
            while True:
                if self._polls_disconnect(request) and await request.is_disconnected():
                    yield None
                    return
                if pending is None:
                    pending = asyncio.create_task(iterator.__anext__())
                    turn.inflight = pending
                if watcher is None or watcher.done():
                    watcher = asyncio.create_task(self._watch_disconnect(request))
                    turn.watcher = watcher
                done, _ = await asyncio.wait(
                    {pending, watcher},
                    timeout=self._settings.sse_ping_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    yield PING
                    continue
                if pending in done:
                    if watcher is not None and not watcher.done():
                        watcher.cancel()
                    watcher = None
                    try:
                        piece = pending.result()
                    except StopAsyncIteration:
                        pending = None
                        turn.inflight = None
                        return
                    turn.inflight = None
                    pending = None
                    if self._polls_disconnect(request) and await request.is_disconnected():
                        yield None
                        return
                    if isinstance(piece, str):
                        piece = ModelChunk(content=piece)
                    yield piece
                    continue
                if pending is not None and not pending.done():
                    pending.cancel()
                yield None
                return
        finally:
            if watcher is not None and not watcher.done():
                watcher.cancel()
            if pending is not None:
                await self._drain_read(pending)
            aclose = getattr(iterator, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    log.warning("stream_close_failed", conversation_id=turn.conversation_id)

    async def _drain_read(self, pending: asyncio.Task) -> None:
        if not pending.done():
            pending.cancel()
        try:
            await pending
        except (asyncio.CancelledError, StopAsyncIteration, Exception):
            return

    def _polls_disconnect(self, request) -> bool:
        """Testes sem ASGI só têm is_disconnected. No FastAPI esperamos o receive real."""
        return getattr(request, "receive", None) is None

    async def _watch_disconnect(self, request) -> None:
        if not self._polls_disconnect(request):
            # O StreamingResponse já espera http.disconnect e cancela esta tarefa.
            await asyncio.Event().wait()
            return
        while True:
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.05)

    def _schedule_cancel_cleanup(self, turn: PreparedTurn, content: str) -> None:
        """O cancelamento do ASGI impede awaits nesta tarefa. A gravação corre noutra."""
        turn.deferred_release = True
        task = asyncio.create_task(self._cancel_cleanup(turn, content))
        self._cleanups.add(task)
        task.add_done_callback(self._cleanups.discard)

    async def _cancel_cleanup(self, turn: PreparedTurn, content: str) -> None:
        try:
            abort = getattr(self._llm, "abort_streams", None)
            if abort is not None:
                await abort()
            for job in (turn.inflight, turn.watcher):
                if job is not None:
                    await self._drain_read(job)
            turn.inflight = None
            turn.watcher = None
            await self.persist_assistant(turn, content, "interrupted")
        finally:
            turn.deferred_release = False
            self.release(turn)

    async def persist_assistant(self, turn: PreparedTurn, content: str, status: str) -> TurnResult:
        async with self._sessions() as session:
            conversation = await session.get(Conversation, turn.conversation_id)
            if conversation is None:
                raise AppError(404, "Conversa não encontrada.")
            user_message = await session.get(Message, turn.user_message_id)
            created = utcnow()
            if user_message is not None and created <= ensure_utc(user_message.created_at):
                created = ensure_utc(user_message.created_at) + timedelta(microseconds=1)
            assistant = Message(
                id=str(uuid4()),
                conversation_id=conversation.id,
                role="assistant",
                content=content,
                status=status,
                created_at=created,
            )
            session.add(assistant)
            title = conversation.title
            conversation.updated_at = utcnow()
            await session.commit()
            await session.refresh(assistant)
            title = conversation.title
            assistant_id = assistant.id
            assistant_role = assistant.role
            assistant_content = assistant.content
            assistant_status = assistant.status
            assistant_created = assistant.created_at
        log.info(
            "chat_turn",
            conversation_id=turn.conversation_id,
            status=status,
            history_truncated=turn.history_truncated,
            output_chars=len(content),
        )
        saved = Message(
            id=assistant_id,
            conversation_id=turn.conversation_id,
            role=assistant_role,
            content=assistant_content,
            status=assistant_status,
            created_at=assistant_created,
        )
        return TurnResult(
            conversation_id=turn.conversation_id,
            title=title,
            history_truncated=turn.history_truncated,
            message=saved,
        )

    def release(self, turn: PreparedTurn) -> None:
        if turn.deferred_release:
            return
        if turn.slot_held:
            self._gate.release_slot()
            turn.slot_held = False
        if turn.conv_held:
            self._gate.release_conversation(turn.conversation_id)
            turn.conv_held = False

    async def _maybe_title(self, user_message: str, fallback: str) -> str:
        try:
            raw = await self._llm.complete(
                title_messages(user_message),
                temperature=None,
                max_tokens=self._settings.title_max_tokens,
                timeout=20,
            )
        except Exception:
            log.warning("title_failed", conversation_fallback=True)
            return fallback
        text = raw.content if isinstance(raw, ModelReply) else str(raw)
        cleaned = clean_title(text)
        return cleaned or fallback

    async def _write_title(self, turn: PreparedTurn) -> str | None:
        """Substitui o título provisório. Devolve o novo título, ou None se ficou igual."""
        async with self._sessions() as session:
            conversation = await session.get(Conversation, turn.conversation_id)
            if conversation is None:
                return None
            current_title = conversation.title
        if turn.user_message_count != 1 or current_title != turn.provisional_title:
            return None
        generated = await self._maybe_title(turn.first_user_content, current_title)
        if not generated or generated == current_title:
            return None
        async with self._sessions() as session:
            conversation = await session.get(Conversation, turn.conversation_id)
            if conversation is None or conversation.title != turn.provisional_title:
                return None
            conversation.title = generated
            conversation.updated_at = utcnow()
            await session.commit()
        return generated

    async def _title_if_slot_free(self, turn: PreparedTurn) -> str | None:
        acquired = await self._gate.try_acquire_slot()
        if not acquired:
            log.info("title_skipped", conversation_id=turn.conversation_id)
            return None
        try:
            return await self._write_title(turn)
        except Exception:
            log.warning("title_failed", conversation_id=turn.conversation_id)
            return None
        finally:
            self._gate.release_slot()

    async def _prepare_new(
        self,
        message: str,
        temperature: float | None,
        max_tokens: int,
    ) -> PreparedTurn:
        llm_messages, truncated = build_context(
            system_prompt=self._settings.effective_system_prompt(self._settings.system_prompt),
            history=[],
            current_user=message,
            max_tokens=max_tokens,
            ctx_size=self._settings.llama_ctx_size,
            reserve=self._settings.context_reserve_tokens,
            chars_per_token=self._settings.chars_per_token,
            message_overhead=self._settings.message_overhead_tokens,
        )
        now = utcnow()
        conversation = Conversation(
            id=str(uuid4()),
            title=provisional_title(message, self._settings.title_max_chars),
            system_prompt=self._settings.system_prompt,
            created_at=now,
            updated_at=now,
        )
        user_message = Message(
            id=str(uuid4()),
            conversation_id=conversation.id,
            role="user",
            content=message,
            status="complete",
            created_at=now,
        )
        async with self._sessions() as session:
            session.add(conversation)
            session.add(user_message)
            await session.commit()
        turn = self._turn(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            title=conversation.title,
            first_user_content=message,
            user_message_count=1,
            llm_messages=llm_messages,
            truncated=truncated,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        await self._acquire(turn)
        return turn

    async def _prepare_existing(
        self,
        conversation_id: str,
        message: str,
        temperature: float | None,
        max_tokens: int,
        regenerate: bool = False,
        attachment_ids: list[str] | None = None,
    ) -> PreparedTurn:
        pending_ids = attachment_ids or []
        async with self._sessions() as session:
            existing = await session.get(Conversation, conversation_id)
            if existing is None:
                raise AppError(404, "Conversa não encontrada.")
        await self._gate.acquire_conversation(conversation_id)
        slot_held = False
        try:
            slot_held = await self._gate.try_acquire_slot()
            if not slot_held:
                raise LLMCapacity()
            async with self._sessions() as session:
                conversation = await session.get(Conversation, conversation_id)
                if conversation is None:
                    raise AppError(404, "Conversa não encontrada.")
                rows = (
                    await session.scalars(
                        select(Message)
                        .where(Message.conversation_id == conversation.id)
                        .order_by(Message.created_at.asc(), Message.id.asc())
                    )
                ).all()
                if regenerate:
                    user_indexes = [index for index, row in enumerate(rows) if row.role == "user"]
                    if not user_indexes:
                        raise NothingToRegenerate()
                    last_user_index = user_indexes[-1]
                    prior_rows = rows[:last_user_index]
                    user_message = rows[last_user_index]
                    typed_text = user_message.content
                    user_count = len(user_indexes)
                    attachment_map = await load_attachments_for_messages(
                        session, [user_message.id]
                    )
                    user_attachments = attachment_map.get(user_message.id, [])
                    prompt_user = compose_user_content(typed_text, user_attachments)
                else:
                    prior_rows = list(rows)
                    typed_text = message
                    user_count = sum(1 for row in rows if row.role == "user") + 1
                    user_message = Message(
                        id=str(uuid4()),
                        conversation_id=conversation.id,
                        role="user",
                        content=typed_text,
                        status="complete",
                        created_at=utcnow(),
                    )
                    session.add(user_message)
                    await session.flush()
                    linked = await link_attachments(
                        session,
                        conversation_id=conversation.id,
                        message_id=user_message.id,
                        attachment_ids=pending_ids,
                    )
                    prompt_user = compose_user_content(typed_text, linked)
                prior_user_ids = [row.id for row in prior_rows if row.role == "user"]
                attachment_map = await load_attachments_for_messages(session, prior_user_ids)
                history = prompt_messages(
                    [
                        _public_message(row, attachment_map.get(row.id, []))
                        for row in prior_rows
                    ]
                )
                llm_messages, truncated = build_context(
                    system_prompt=self._settings.effective_system_prompt(
                        conversation.system_prompt
                    ),
                    history=history,
                    current_user=prompt_user,
                    max_tokens=max_tokens,
                    ctx_size=self._settings.llama_ctx_size,
                    reserve=self._settings.context_reserve_tokens,
                    chars_per_token=self._settings.chars_per_token,
                    message_overhead=self._settings.message_overhead_tokens,
                )
                conversation.updated_at = utcnow()
                await session.commit()
                await session.refresh(user_message)
                saved_title = conversation.title
                saved_user_id = user_message.id
                saved_conversation_id = conversation.id
            first_user = next(
                (row.content for row in prior_rows if row.role == "user"),
                typed_text,
            )
            return self._turn(
                conversation_id=saved_conversation_id,
                user_message_id=saved_user_id,
                title=saved_title,
                first_user_content=first_user,
                user_message_count=user_count,
                llm_messages=llm_messages,
                truncated=truncated,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception:
            if slot_held:
                self._gate.release_slot()
            self._gate.release_conversation(conversation_id)
            raise

    async def _acquire(self, turn: PreparedTurn) -> None:
        await self._gate.acquire_conversation(turn.conversation_id)
        slot_held = False
        try:
            slot_held = await self._gate.try_acquire_slot()
            if not slot_held:
                raise LLMCapacity()
        except Exception:
            if slot_held:
                self._gate.release_slot()
            self._gate.release_conversation(turn.conversation_id)
            turn.slot_held = False
            turn.conv_held = False
            raise

    def _turn(
        self,
        *,
        conversation_id: str,
        user_message_id: str,
        title: str,
        first_user_content: str,
        user_message_count: int,
        llm_messages: list[dict[str, str]],
        truncated: bool,
        max_tokens: int,
        temperature: float | None,
    ) -> PreparedTurn:
        return PreparedTurn(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            title=title,
            provisional_title=provisional_title(first_user_content, self._settings.title_max_chars),
            llm_messages=llm_messages,
            history_truncated=truncated,
            context_tokens=prompt_token_estimate(
                llm_messages,
                self._settings.chars_per_token,
                self._settings.message_overhead_tokens,
            ),
            context_limit=self._settings.llama_ctx_size,
            user_message_count=user_message_count,
            first_user_content=first_user_content,
            max_tokens=max_tokens,
            temperature=temperature,
        )


def _public_message(
    row: Message,
    attachments: list[AttachmentRecord] | None = None,
) -> dict[str, str]:
    content = row.content
    if row.role == "user" and attachments:
        content = compose_user_content(row.content, attachments)
    return {"role": row.role, "content": content, "status": row.status, "id": row.id}
