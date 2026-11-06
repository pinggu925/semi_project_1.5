from datetime import datetime, timezone

import pytest

from app.analyzer.types import RepResult
from app.redis_store import SessionStore

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store(redis_client, clean_db):
    return SessionStore(redis_client, ttl_seconds=3600)


async def test_create_sets_hash_and_ttl(store, redis_client):
    await store.create("s1", "c1", "squat", NOW)
    state = await store.get_state("s1")
    assert state["client_id"] == "c1" and state["exercise"] == "squat" and state["rep_count"] == "0"
    assert 0 < await redis_client.ttl("session:s1") <= 3600


async def test_angles_list_keeps_last_five(store, redis_client):
    await store.create("s1", "c1", "squat", NOW)
    for a in range(8):
        await store.push_angle("s1", float(a))
    assert await redis_client.lrange("session:s1:angles", 0, -1) == ["7.0", "6.0", "5.0", "4.0", "3.0"]


async def test_every_write_refreshes_ttl(store, redis_client):
    await store.create("s1", "c1", "squat", NOW)
    await store.push_angle("s1", 1.0)
    await store.append_rep("s1", RepResult(1, 90, 10, 100, []), NOW)
    for key in ("session:s1", "session:s1:angles", "session:s1:reps"):
        await redis_client.expire(key, 5)
    await store.update_state("s1", "DESCENDING", 1, 120.0)
    await store.push_angle("s1", 2.0)
    await store.append_rep("s1", RepResult(2, 90, 10, 100, []), NOW)
    for key in ("session:s1", "session:s1:angles", "session:s1:reps"):
        assert await redis_client.ttl(key) > 3000


async def test_reps_round_trip_in_order(store):
    await store.create("s1", "c1", "squat", NOW)
    await store.append_rep("s1", RepResult(1, 88.5, 12.0, 100, []), NOW)
    await store.append_rep("s1", RepResult(2, 120.0, 50.0, 50, ["DEPTH_SHALLOW", "TRUNK_LEAN"]), NOW)
    reps = await store.get_reps("s1")
    assert [r["rep_no"] for r in reps] == [1, 2]
    assert reps[1]["feedback_codes"] == ["DEPTH_SHALLOW", "TRUNK_LEAN"]
    assert reps[0]["min_knee_angle"] == 88.5 and reps[0]["performed_at"] == NOW.isoformat()


async def test_sessions_are_isolated(store):
    await store.create("a", "c1", "squat", NOW)
    await store.create("b", "c1", "squat", NOW)
    await store.append_rep("a", RepResult(1, 90, 10, 100, []), NOW)
    assert len(await store.get_reps("a")) == 1 and await store.get_reps("b") == []


async def test_update_state_and_none_min_angle(store):
    await store.create("s1", "c1", "squat", NOW)
    await store.update_state("s1", "BOTTOM", 3, None)
    state = await store.get_state("s1")
    assert state["phase"] == "BOTTOM" and state["rep_count"] == "3" and state["current_min_angle"] == ""


async def test_delete_removes_all_three_keys(store, redis_client):
    await store.create("s1", "c1", "squat", NOW)
    await store.push_angle("s1", 1.0)
    await store.append_rep("s1", RepResult(1, 90, 10, 100, []), NOW)
    await store.delete("s1")
    assert await redis_client.exists("session:s1", "session:s1:angles", "session:s1:reps") == 0


async def test_get_state_missing_returns_none(store):
    assert await store.get_state("nope") is None
