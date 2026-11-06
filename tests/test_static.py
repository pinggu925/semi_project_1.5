import httpx

from app.config import Settings
from app.main import create_app


async def _get(path):
    app = create_app(Settings())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(path)


async def test_index_is_served():
    r = await _get("/")
    assert r.status_code == 200 and "<title>Squat Coach</title>" in r.text


async def test_scripts_are_served():
    for path in ("/app.js", "/workout.js", "/style.css"):
        assert (await _get(path)).status_code == 200, path


async def test_api_routes_win_over_static():
    r = await _get("/api/sessions")  # no client_id -> validation, not a static 404
    assert r.status_code == 422
