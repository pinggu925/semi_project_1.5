import httpx
import pytest
from tests.asgi_lifespan_shim import lifespan_client

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration


async def test_health_ok(settings):
    async with lifespan_client(create_app(settings)) as client:
        r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"postgres": "ok", "redis": "ok"}


async def test_health_reports_redis_down(settings):
    bad = Settings(database_url=settings.database_url, redis_url="redis://localhost:6399/0")
    async with lifespan_client(create_app(bad)) as client:
        r = await client.get("/health")
    body = r.json()
    assert r.status_code == 503 and body["redis"] == "down" and body["postgres"] == "ok"
