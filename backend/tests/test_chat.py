import asyncio

import pytest

from app.services.chat import ChatService
from app.services.titles import provisional_title
from tests.conftest import FakeLLM, make_client, parse_sse


async def test_chat_json_creates_conversation(tmp_path):
    async with make_client(tmp_path) as (client, fake, _app):
        response = await client.post("/api/chat", json={"message": "Qual é a capital da França?"})
        assert response.status_code == 200
        body = response.json()
        assert body["message"]["content"] == "Olá mundo"
        assert body["message"]["role"] == "assistant"
        assert body["history_truncated"] is False
        assert fake.complete_calls[0]["temperature"] is None
        assert fake.complete_calls[0]["max_tokens"] == 128
        assert fake.complete_calls[0]["messages"][0] == {"role": "system", "content": "SYS"}


async def test_temperature_and_max_tokens_are_forwarded(tmp_path):
    async with make_client(tmp_path) as (client, fake, _app):
        response = await client.post(
            "/api/chat",
            json={"message": "oi", "temperature": 0.2, "max_tokens": 32},
        )
        assert response.status_code == 200
        call = fake.complete_calls[0]
        assert call["temperature"] == 0.2
        assert call["max_tokens"] == 32


async def test_history_is_sent_on_the_next_turn(tmp_path):
    async with make_client(tmp_path) as (client, fake, _app):
        created = await client.post("/api/chat", json={"message": "Meu nome é Ana."})
        conversation_id = created.json()["conversation_id"]
        follow = await client.post(
            "/api/chat",
            json={"conversation_id": conversation_id, "message": "Qual é o meu nome?"},
        )
        assert follow.status_code == 200
        contents = [item["content"] for item in fake.complete_calls[-1]["messages"]]
        assert "Meu nome é Ana." in contents
        assert "Olá mundo" in contents
        assert "Qual é o meu nome?" in contents


async def test_stream_events_and_headers(tmp_path):
    async with make_client(tmp_path) as (client, fake, _app):
        fake.chunks = ["Olá", " mundo"]
        response = await client.post("/api/chat/stream", json={"message": "oi"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "no-cache"
        events = parse_sse(response.text)
        kinds = [kind for kind, _payload in events]
        assert kinds[0] == "meta"
        assert "token" in kinds
        assert "done" in kinds
        assert kinds.index("done") < kinds.index("title")
        meta = events[0][1]
        assert meta["context_tokens"] == 10
        assert meta["context_limit"] == 8192
        done = next(payload for kind, payload in events if kind == "done")
        assert done["content"] == "Olá mundo"
        assert done["finish_reason"] == "stop"
        assert done["context_tokens"] == 10
        assert done["tokens_per_second"] is None
        assert done["prompt_tokens"] is None
        assert events[-1][0] == "title"

        fake.prompt_tokens = 20
        fake.completion_tokens = 2
        measured = await client.post("/api/chat/stream", json={"message": "oi"})
        measured_events = parse_sse(measured.text)
        measured_done = next(payload for kind, payload in measured_events if kind == "done")
        assert measured_done["prompt_tokens"] == 20
        assert measured_done["completion_tokens"] == 2
        assert measured_done["context_tokens"] == 22
        assert measured_done["tokens_per_second"] > 0


async def test_stream_strips_think_blocks(tmp_path):
    async with make_client(tmp_path) as (client, fake, _app):
        fake.chunks = ["<think>", "segredo", "</think>", "Resposta"]
        response = await client.post("/api/chat/stream", json={"message": "oi"})
        events = parse_sse(response.text)
        assert "think" not in [kind for kind, _payload in events]
        done = next(payload for kind, payload in events if kind == "done")
        assert done["content"] == "Resposta"
        assert done.get("thinking") is None
        assert "segredo" not in done["content"]


async def test_stream_emits_think_when_enabled(tmp_path):
    async with make_client(tmp_path, llama_enable_thinking=True) as (client, fake, _app):
        fake.chunks = ["<think>", "segredo", "</think>", "Resposta"]
        response = await client.post("/api/chat/stream", json={"message": "oi"})
        events = parse_sse(response.text)
        think_deltas = [payload["delta"] for kind, payload in events if kind == "think"]
        assert "".join(think_deltas) == "segredo"
        done = next(payload for kind, payload in events if kind == "done")
        assert done["content"] == "Resposta"
        assert done["thinking"] == "segredo"
        assert "think" in [kind for kind, _payload in events]


async def test_stream_emits_reasoning_field_when_enabled(tmp_path):
    async with make_client(tmp_path, llama_enable_thinking=True) as (client, fake, _app):
        fake.chunks = ["Resposta"]
        fake.chunk_reasoning = ["pensando", ""]
        response = await client.post("/api/chat/stream", json={"message": "oi"})
        events = parse_sse(response.text)
        think_deltas = [payload["delta"] for kind, payload in events if kind == "think"]
        assert think_deltas == ["pensando"]
        done = next(payload for kind, payload in events if kind == "done")
        assert done["thinking"] == "pensando"
        assert done["content"] == "Resposta"


async def test_stream_error_event_persists_status(tmp_path):
    async with make_client(tmp_path) as (client, fake, _app):
        fake.fail_stream = True
        response = await client.post("/api/chat/stream", json={"message": "oi"})
        events = parse_sse(response.text)
        assert events[0][0] == "meta"
        assert events[-1][0] == "error"
        conversation_id = events[0][1]["conversation_id"]
        detail = await client.get(f"/api/conversations/{conversation_id}")
        assert detail.json()["messages"][-1]["status"] == "error"


async def test_auth_rejects_and_accepts_bearer(tmp_path):
    async with make_client(tmp_path, api_key="secret") as (client, _fake, _app):
        denied = await client.post("/api/chat", json={"message": "oi"})
        assert denied.status_code == 401
        wrong = await client.post(
            "/api/chat",
            json={"message": "oi"},
            headers={"Authorization": "Bearer outro"},
        )
        assert wrong.status_code == 401
        allowed = await client.post(
            "/api/chat",
            json={"message": "oi"},
            headers={"Authorization": "Bearer secret"},
        )
        assert allowed.status_code == 200


async def test_message_over_context_is_413_and_creates_nothing(tmp_path):
    async with make_client(
        tmp_path,
        llama_ctx_size=40,
        max_tokens_default=10,
        context_reserve_tokens=10,
        chars_per_token=1,
    ) as (client, _fake, _app):
        response = await client.post("/api/chat", json={"message": "x" * 30})
        assert response.status_code == 413
        listed = await client.get("/api/conversations")
        assert listed.json()["conversations"] == []


async def test_long_history_is_truncated(tmp_path):
    async with make_client(
        tmp_path,
        llama_ctx_size=80,
        max_tokens_default=20,
        context_reserve_tokens=10,
        chars_per_token=1,
        system_prompt="SYS",
    ) as (client, fake, _app):
        fake.reply = "123456789"
        fake.title_fails = True
        conversation_id = None
        last = None
        for index in range(5):
            last = await client.post(
                "/api/chat",
                json={"conversation_id": conversation_id, "message": "12345"},
            )
            assert last.status_code == 200
            conversation_id = last.json()["conversation_id"]
        assert last.json()["history_truncated"] is True
        sent = [item["content"] for item in fake.complete_calls[-1]["messages"]]
        assert sent[0] == "SYS"
        assert sent[-1] == "12345"


async def test_title_is_replaced_after_the_first_reply(tmp_path):
    async with make_client(tmp_path) as (client, fake, _app):
        message = "Quero um título automático para esta conversa de teste longa"
        response = await client.post("/api/chat", json={"message": message})
        assert response.status_code == 200
        assert response.json()["title"] == "Titulo curto"
        assert response.json()["title"] != provisional_title(message)


async def test_title_keeps_provisional_when_generation_fails(tmp_path):
    async with make_client(tmp_path) as (client, fake, _app):
        fake.title_fails = True
        message = "Quero um título automático para esta conversa de teste longa"
        response = await client.post("/api/chat", json={"message": message})
        assert response.json()["title"] == provisional_title(message)


async def test_disconnect_saves_interrupted_partial(tmp_path):
    async with make_client(tmp_path) as (client, fake, app):
        fake.chunks = ["um", "dois", "tres"]
        service = ChatService(
            sessionmaker=app.state.sessionmaker,
            llm=fake,
            gate=app.state.gate,
            settings=app.state.settings,
        )
        turn = await service.prepare(None, "oi", temperature=None, max_tokens=None)

        class Request:
            def __init__(self) -> None:
                self.disconnected = False

            async def is_disconnected(self) -> bool:
                return self.disconnected

        request = Request()
        events = []
        try:
            async for kind, _payload in service.generate_stream(turn, request):
                events.append(kind)
                request.disconnected = True
        finally:
            service.release(turn)
        assert events == ["token"]
        detail = await client.get(f"/api/conversations/{turn.conversation_id}")
        assistant = detail.json()["messages"][-1]
        assert assistant["status"] == "interrupted"
        assert assistant["content"] == "um"


async def test_disconnect_during_prompt_saves_interrupted(tmp_path):
    async with make_client(tmp_path) as (client, fake, app):
        fake.block = True
        fake.chunks = ["tarde demais"]
        service = ChatService(
            sessionmaker=app.state.sessionmaker,
            llm=fake,
            gate=app.state.gate,
            settings=app.state.settings,
        )
        turn = await service.prepare(None, "oi", temperature=None, max_tokens=None)

        class Request:
            def __init__(self) -> None:
                self.disconnected = False

            async def is_disconnected(self) -> bool:
                return self.disconnected

        request = Request()

        async def stop_when_started() -> None:
            await fake.started.wait()
            request.disconnected = True

        watcher = asyncio.create_task(stop_when_started())
        events = []
        try:
            async for kind, _payload in service.generate_stream(turn, request):
                events.append(kind)
        finally:
            service.release(turn)
            await watcher
        assert events == []
        detail = await client.get(f"/api/conversations/{turn.conversation_id}")
        assistant = detail.json()["messages"][-1]
        assert assistant["status"] == "interrupted"
        assert assistant["content"] == ""


async def test_task_cancel_during_prompt_saves_interrupted(tmp_path):
    async with make_client(tmp_path) as (client, fake, app):
        fake.block = True
        fake.chunks = ["tarde demais"]
        service = ChatService(
            sessionmaker=app.state.sessionmaker,
            llm=fake,
            gate=app.state.gate,
            settings=app.state.settings,
        )
        turn = await service.prepare(None, "oi", temperature=None, max_tokens=None)

        class Request:
            async def receive(self) -> dict:
                await asyncio.Event().wait()
                return {"type": "http.disconnect"}

        async def consume() -> None:
            async for _kind, _payload in service.generate_stream(turn, Request()):
                pass

        job = asyncio.create_task(consume())
        await fake.started.wait()
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        await asyncio.wait_for(asyncio.gather(*service._cleanups), timeout=2)
        detail = await client.get(f"/api/conversations/{turn.conversation_id}")
        assistant = detail.json()["messages"][-1]
        assert assistant["status"] == "interrupted"
        assert assistant["content"] == ""


async def test_second_generation_on_same_conversation_is_409(tmp_path):
    async with make_client(tmp_path) as (client, fake, _app):
        created = await client.post("/api/chat", json={"message": "primeira"})
        conversation_id = created.json()["conversation_id"]
        fake.block = True
        fake.started.clear()
        fake.release.clear()

        async def read_stream() -> str:
            async with client.stream(
                "POST",
                "/api/chat/stream",
                json={"conversation_id": conversation_id, "message": "segunda"},
            ) as response:
                chunks = []
                async for chunk in response.aiter_text():
                    chunks.append(chunk)
                return "".join(chunks)

        reader = asyncio.create_task(read_stream())
        try:
            await asyncio.wait_for(fake.started.wait(), timeout=5)
            conflict = await client.post(
                "/api/chat",
                json={"conversation_id": conversation_id, "message": "terceira"},
            )
            assert conflict.status_code == 409
        finally:
            fake.release.set()
            body = await asyncio.wait_for(reader, timeout=5)
        assert "event: done" in body


async def test_no_free_slot_returns_503(tmp_path):
    async with make_client(tmp_path) as (client, fake, _app):
        first = await client.post("/api/chat", json={"message": "conversa a"})
        second = await client.post("/api/chat", json={"message": "conversa b"})
        conversation_a = first.json()["conversation_id"]
        conversation_b = second.json()["conversation_id"]
        fake.block = True
        fake.started.clear()
        fake.release.clear()

        async def read_stream() -> None:
            async with client.stream(
                "POST",
                "/api/chat/stream",
                json={"conversation_id": conversation_a, "message": "ocupa o slot"},
            ) as response:
                async for _chunk in response.aiter_text():
                    pass

        reader = asyncio.create_task(read_stream())
        try:
            await asyncio.wait_for(fake.started.wait(), timeout=5)
            busy = await client.post(
                "/api/chat",
                json={"conversation_id": conversation_b, "message": "não cabe"},
            )
            assert busy.status_code == 503
        finally:
            fake.release.set()
            await asyncio.wait_for(reader, timeout=5)


def _chat_calls(fake):
    return [
        call
        for call in fake.complete_calls
        if "título" not in call["messages"][0]["content"].lower()
    ]


async def test_regenerate_reuses_user_and_skips_partial(tmp_path):
    async with make_client(tmp_path) as (client, fake, app):
        created = await client.post("/api/chat", json={"message": "Pergunta original"})
        conversation_id = created.json()["conversation_id"]
        detail = await client.get(f"/api/conversations/{conversation_id}")
        assistant_id = detail.json()["messages"][-1]["id"]
        async with app.state.sessionmaker() as session:
            from app.db.models import Message

            assistant = await session.get(Message, assistant_id)
            assistant.status = "interrupted"
            assistant.content = "parcial secreto"
            await session.commit()
        fake.complete_calls.clear()
        again = await client.post(
            "/api/chat",
            json={"conversation_id": conversation_id, "message": "outro texto", "regenerate": True},
        )
        assert again.status_code == 200
        detail = await client.get(f"/api/conversations/{conversation_id}")
        messages = detail.json()["messages"]
        users = [item for item in messages if item["role"] == "user"]
        assert len(users) == 1
        assert users[0]["content"] == "Pergunta original"
        sent = [item["content"] for item in _chat_calls(fake)[-1]["messages"]]
        assert "Pergunta original" in sent
        assert "parcial secreto" not in sent
        assert "outro texto" not in sent


async def test_new_message_after_partial_omits_it_from_prompt(tmp_path):
    async with make_client(tmp_path) as (client, fake, app):
        created = await client.post("/api/chat", json={"message": "Pergunta original"})
        conversation_id = created.json()["conversation_id"]
        detail = await client.get(f"/api/conversations/{conversation_id}")
        assistant_id = detail.json()["messages"][-1]["id"]
        async with app.state.sessionmaker() as session:
            from app.db.models import Message

            assistant = await session.get(Message, assistant_id)
            assistant.status = "interrupted"
            assistant.content = "parcial secreto"
            await session.commit()
        fake.complete_calls.clear()
        follow = await client.post(
            "/api/chat",
            json={"conversation_id": conversation_id, "message": "nova pergunta"},
        )
        assert follow.status_code == 200
        detail = await client.get(f"/api/conversations/{conversation_id}")
        users = [item for item in detail.json()["messages"] if item["role"] == "user"]
        assert [item["content"] for item in users] == ["Pergunta original", "nova pergunta"]
        sent = [item["content"] for item in _chat_calls(fake)[-1]["messages"]]
        assert sent[-1] == "nova pergunta"
        assert "parcial secreto" not in sent
        assert "Pergunta original" not in sent


async def test_regenerate_without_user_message_is_422(tmp_path):
    async with make_client(tmp_path) as (client, _fake, _app):
        missing = await client.post("/api/chat", json={"message": "oi", "regenerate": True})
        assert missing.status_code == 422


async def test_attachment_flow_composes_prompt(tmp_path):
    async with make_client(tmp_path) as (client, fake, _app):
        upload = await client.post(
            "/api/attachments",
            files=[("files", ("codigo.py", b"print(1)", "text/plain"))],
        )
        assert upload.status_code == 200
        body = upload.json()
        conversation_id = body["conversation_id"]
        attachment_id = body["attachments"][0]["id"]

        response = await client.post(
            "/api/chat",
            json={
                "conversation_id": conversation_id,
                "message": "Explique",
                "attachment_ids": [attachment_id],
            },
        )
        assert response.status_code == 200
        user_messages = [
            item["content"]
            for item in fake.complete_calls[-1]["messages"]
            if item["role"] == "user"
        ]
        assert any("Anexo: codigo.py" in item for item in user_messages)
        assert any("print(1)" in item for item in user_messages)

        detail = await client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        stored = detail.json()["messages"][0]
        assert stored["content"] == "Explique"
        assert stored["attachments"][0]["filename"] == "codigo.py"


async def test_chat_requires_message_or_attachment(tmp_path):
    async with make_client(tmp_path) as (client, _fake, _app):
        response = await client.post("/api/chat", json={"message": "   "})
        assert response.status_code == 422


async def test_stream_emits_done_before_title(tmp_path):
    async with make_client(tmp_path) as (client, fake, app):
        service = ChatService(
            sessionmaker=app.state.sessionmaker,
            llm=fake,
            gate=app.state.gate,
            settings=app.state.settings,
        )
        turn = await service.prepare(None, "Quero um título longo o bastante", temperature=None, max_tokens=None)

        class Request:
            async def is_disconnected(self) -> bool:
                return False

        events = []
        try:
            async for kind, _payload in service.generate_stream(turn, Request()):
                events.append(kind)
                if kind == "done":
                    titles = [
                        call
                        for call in fake.complete_calls
                        if "título" in call["messages"][0]["content"].lower()
                    ]
                    assert titles == []
        finally:
            service.release(turn)
        assert "done" in events
        assert events.index("done") < events.index("title")
        detail = await client.get(f"/api/conversations/{turn.conversation_id}")
        assert detail.json()["title"] == "Titulo curto"


async def test_stream_skips_title_when_slot_is_busy(tmp_path):
    async with make_client(tmp_path) as (client, fake, app):
        service = ChatService(
            sessionmaker=app.state.sessionmaker,
            llm=fake,
            gate=app.state.gate,
            settings=app.state.settings,
        )
        turn = await service.prepare(None, "Quero um título longo o bastante", temperature=None, max_tokens=None)

        async def no_slot() -> bool:
            return False

        app.state.gate.try_acquire_slot = no_slot

        class Request:
            async def is_disconnected(self) -> bool:
                return False

        events = []
        try:
            async for kind, _payload in service.generate_stream(turn, Request()):
                events.append(kind)
        finally:
            service.release(turn)
        assert "done" in events
        assert "title" not in events
        detail = await client.get(f"/api/conversations/{turn.conversation_id}")
        assert detail.json()["title"] != "Titulo curto"


async def test_stream_emits_ping_while_the_model_is_silent(tmp_path):
    async with make_client(tmp_path, sse_ping_seconds=0.05) as (_client, fake, app):
        fake.block = True
        service = ChatService(
            sessionmaker=app.state.sessionmaker,
            llm=fake,
            gate=app.state.gate,
            settings=app.state.settings,
        )
        turn = await service.prepare(None, "oi", temperature=None, max_tokens=None)

        class Request:
            async def is_disconnected(self) -> bool:
                return False

        events = []

        async def consume() -> None:
            async for kind, _payload in service.generate_stream(turn, Request()):
                events.append(kind)
                if kind == "ping":
                    fake.release.set()

        try:
            await asyncio.wait_for(consume(), timeout=2)
        finally:
            service.release(turn)
        assert events[0] == "ping"
        assert "token" in events
        assert events.index("ping") < events.index("token")


async def test_uvicorn_abort_saves_interrupted(tmp_path):
    import socket

    import httpx
    import uvicorn

    from app.config import Settings
    from app.main import create_app

    database = (tmp_path / "live.db").as_posix()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{database}",
        api_key="",
        llama_base_url="http://127.0.0.1:9/v1",
        llama_parallel=1,
        max_tokens_default=32,
        system_prompt="SYS",
        log_level="WARNING",
    )
    app = create_app(settings)
    fake = FakeLLM()
    fake.chunks = ["um", "dois"]
    fake.pause_after_chunks = 1
    app.state.llm = fake
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    serve = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("o uvicorn não subiu")
        async with httpx.AsyncClient(timeout=5) as client:
            async with client.stream(
                "POST",
                f"http://127.0.0.1:{port}/api/chat/stream",
                json={"message": "oi"},
            ) as response:
                assert response.status_code == 200
                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    if "event: token" in buffer:
                        break
            conversation_id = parse_sse(buffer)[0][1]["conversation_id"]
            assistant = None
            for _ in range(40):
                detail = await client.get(
                    f"http://127.0.0.1:{port}/api/conversations/{conversation_id}"
                )
                messages = detail.json()["messages"]
                if messages and messages[-1]["role"] == "assistant":
                    assistant = messages[-1]
                    if assistant["status"] == "interrupted":
                        break
                await asyncio.sleep(0.05)
            assert assistant is not None
            assert assistant["status"] == "interrupted"
            assert assistant["content"] == "um"
    finally:
        fake.release.set()
        server.should_exit = True
        await serve
