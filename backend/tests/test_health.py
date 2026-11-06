from tests.conftest import make_client


async def test_health_ok(tmp_path):
    async with make_client(tmp_path) as (client, _fake, _app):
        response = await client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["upstream"]["reachable"] is True


async def test_health_degraded(tmp_path):
    async with make_client(tmp_path) as (client, fake, _app):
        fake.health = fake.health.__class__(reachable=False, status="unreachable")
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"


async def test_health_is_public_when_auth_is_on(tmp_path):
    async with make_client(tmp_path, api_key="secret") as (client, _fake, _app):
        response = await client.get("/health")
        assert response.status_code == 200
        denied = await client.get("/api/conversations")
        assert denied.status_code == 401
