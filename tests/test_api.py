import uuid
from datetime import datetime, timedelta, timezone

import pytest
from tests.asgi_lifespan_shim import lifespan_client

from app.main import create_app
from app.repository import save_session, upsert_client

pytestmark = pytest.mark.integration
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)  # Monday


def rep(no, score, codes=()):
    return {"rep_no": no, "min_knee_angle": 90.0, "max_trunk_tilt": 10.0, "score": score,
            "feedback_codes": list(codes), "performed_at": T0.isoformat()}


async def seed(maker, client_id, started_at, scores, codes=()):
    sid = str(uuid.uuid4())
    async with maker() as db, db.begin():
        await upsert_client(db, client_id, "tester")
        await save_session(db, sid, client_id, "squat", started_at, started_at + timedelta(minutes=5),
                           "completed", [rep(i + 1, s, codes) for i, s in enumerate(scores)])
    return sid


@pytest.fixture
async def api(settings, clean_db):
    async with lifespan_client(create_app(settings)) as client:
        yield client


async def test_sessions_list_newest_first_with_pagination(api, sessionmaker):
    cid = str(uuid.uuid4())
    ids = [await seed(sessionmaker, cid, T0 + timedelta(days=i), [80]) for i in range(3)]
    r = await api.get("/api/sessions", params={"client_id": cid, "limit": 2, "offset": 0})
    body = r.json()
    assert r.status_code == 200 and body["total"] == 3
    assert [s["id"] for s in body["items"]] == [ids[2], ids[1]]
    r2 = await api.get("/api/sessions", params={"client_id": cid, "limit": 2, "offset": 2})
    assert [s["id"] for s in r2.json()["items"]] == [ids[0]]


async def test_score_delta_uses_previous_session(api, sessionmaker):
    cid = str(uuid.uuid4())
    await seed(sessionmaker, cid, T0, [80])
    await seed(sessionmaker, cid, T0 + timedelta(days=1), [90])
    items = (await api.get("/api/sessions", params={"client_id": cid})).json()["items"]
    assert items[0]["score_delta"] == 10.0 and items[1]["score_delta"] is None


async def test_session_detail_includes_reps_in_order(api, sessionmaker):
    cid = str(uuid.uuid4())
    sid = await seed(sessionmaker, cid, T0, [100, 60, 80], codes=["DEPTH_SHALLOW"])
    body = (await api.get(f"/api/sessions/{sid}")).json()
    assert body["id"] == sid and body["total_reps"] == 3 and body["avg_score"] == 80.0
    assert [r["rep_no"] for r in body["reps"]] == [1, 2, 3]
    assert body["reps"][1]["score"] == 60 and body["reps"][0]["feedback_codes"] == ["DEPTH_SHALLOW"]


async def test_session_detail_404(api):
    assert (await api.get(f"/api/sessions/{uuid.uuid4()}")).status_code == 404


async def test_session_detail_invalid_id_is_422(api):
    assert (await api.get("/api/sessions/not-a-uuid")).status_code == 422


async def test_summary_totals_and_weekly_buckets(api, sessionmaker):
    cid = str(uuid.uuid4())
    await seed(sessionmaker, cid, T0, [100, 80])                       # week of 11-02
    await seed(sessionmaker, cid, T0 + timedelta(days=2), [60])        # same week
    await seed(sessionmaker, cid, T0 + timedelta(days=7), [90, 90])    # week of 11-09
    body = (await api.get("/api/stats/summary", params={"client_id": cid})).json()
    assert body["total_sessions"] == 3 and body["total_reps"] == 5
    assert body["avg_score"] == 84.0  # rep-weighted: (100+80+60+90+90)/5
    assert [(w["week"], w["reps"], w["avg_score"]) for w in body["weekly"]] == [
        ("2026-11-02", 3, 80.0), ("2026-11-09", 2, 90.0)]


async def test_mistakes_ranking_counts_codes(api, sessionmaker):
    cid = str(uuid.uuid4())
    await seed(sessionmaker, cid, T0, [50, 50, 50], codes=["DEPTH_SHALLOW"])
    await seed(sessionmaker, cid, T0 + timedelta(days=1), [70], codes=["TRUNK_LEAN"])
    body = (await api.get("/api/stats/mistakes", params={"client_id": cid})).json()
    assert body == [{"code": "DEPTH_SHALLOW", "count": 3}, {"code": "TRUNK_LEAN", "count": 1}]


async def test_unknown_client_returns_empty_not_error(api):
    cid = str(uuid.uuid4())
    s = await api.get("/api/sessions", params={"client_id": cid})
    assert s.status_code == 200 and s.json() == {"items": [], "total": 0}
    summary = (await api.get("/api/stats/summary", params={"client_id": cid})).json()
    assert summary == {"total_sessions": 0, "total_reps": 0, "avg_score": None, "weekly": []}
    assert (await api.get("/api/stats/mistakes", params={"client_id": cid})).json() == []


async def test_other_clients_data_is_not_returned(api, sessionmaker):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    await seed(sessionmaker, a, T0, [80], codes=["TRUNK_LEAN"])
    assert (await api.get("/api/sessions", params={"client_id": b})).json()["total"] == 0
    assert (await api.get("/api/stats/mistakes", params={"client_id": b})).json() == []


@pytest.mark.parametrize("path", ["/api/sessions", "/api/stats/summary", "/api/stats/mistakes"])
async def test_invalid_client_id_is_422(api, path):
    assert (await api.get(path, params={"client_id": "nope"})).status_code == 422


async def test_limit_is_bounded(api):
    r = await api.get("/api/sessions", params={"client_id": str(uuid.uuid4()), "limit": 1000})
    assert r.status_code == 422
