from sqlalchemy import func, select

from app.db.models import Message
from tests.conftest import make_client


async def test_list_get_rename_and_delete(tmp_path):
    async with make_client(tmp_path) as (client, _fake, app):
        created = await client.post("/api/chat", json={"message": "Vamos falar de mapas."})
        conversation_id = created.json()["conversation_id"]

        listed = await client.get("/api/conversations")
        assert listed.status_code == 200
        assert listed.json()["conversations"][0]["id"] == conversation_id

        detail = await client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        body = detail.json()
        roles = [item["role"] for item in body["messages"]]
        assert roles == ["user", "assistant"]
        assert body["context_limit"] == 8192
        assert body["context_tokens"] == 22

        renamed = await client.patch(
            f"/api/conversations/{conversation_id}",
            json={"title": "Mapas"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "Mapas"

        missing = await client.get("/api/conversations/11111111-2222-4333-8444-555555555555")
        assert missing.status_code == 404

        deleted = await client.delete(f"/api/conversations/{conversation_id}")
        assert deleted.status_code == 204
        assert deleted.content == b""
        assert (await client.get(f"/api/conversations/{conversation_id}")).status_code == 404

        async with app.state.sessionmaker() as session:
            remaining = await session.scalar(select(func.count()).select_from(Message))
        assert remaining == 0


async def test_invalid_conversation_id_is_422(tmp_path):
    async with make_client(tmp_path) as (client, _fake, _app):
        response = await client.post("/api/chat", json={"conversation_id": "nao-e-uuid", "message": "oi"})
        assert response.status_code == 422
