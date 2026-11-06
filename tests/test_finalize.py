import logging
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.analyzer.types import RepResult
from app.config import Settings
from app.db import create_engine_and_sessionmaker
from app.finalize import finalize_session
from app.redis_store import SessionStore

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store(redis_client, clean_db):
    return SessionStore(redis_client, ttl_seconds=3600)


async def _seed(store, scores, client_id=None, codes=None):
    sid, cid = str(uuid.uuid4()), client_id or str(uuid.uuid4())
    await store.create(sid, cid, "squat", NOW)
    for i, s in enumerate(scores, start=1):
        await store.append_rep(sid, RepResult(i, 90.0, 10.0, s, list(codes or [])), NOW)
    return sid, cid


async def _count(maker, table, where="", **params):
    async with maker() as db:
        return (await db.execute(text(f"SELECT count(*) FROM {table} {where}"), params)).scalar_one()


async def test_finalize_moves_reps_in_one_transaction(store, sessionmaker, redis_client):
    sid, _ = await _seed(store, [100, 80, 60])
    total, avg = await finalize_session(store, sessionmaker, sid, "completed")
    assert (total, avg) == (3, 80.0)
    assert await _count(sessionmaker, "workout_sessions") == 1
    assert await _count(sessionmaker, "reps") == 3
    async with sessionmaker() as db:
        row = (await db.execute(text("SELECT status, total_reps, avg_score FROM workout_sessions"))).one()
    assert row.status == "completed" and row.total_reps == 3 and float(row.avg_score) == 80.0
    assert await redis_client.exists(f"session:{sid}", f"session:{sid}:reps") == 0


async def test_finalize_zero_reps_stores_nothing(store, sessionmaker, redis_client):
    sid, _ = await _seed(store, [])
    assert await finalize_session(store, sessionmaker, sid, "completed") == (0, None)
    assert await _count(sessionmaker, "workout_sessions") == 0
    assert await redis_client.exists(f"session:{sid}") == 0


async def test_abandoned_status_is_stored(store, sessionmaker):
    sid, _ = await _seed(store, [90])
    await finalize_session(store, sessionmaker, sid, "abandoned")
    assert await _count(sessionmaker, "workout_sessions", "WHERE status = 'abandoned'") == 1


async def test_missing_session_returns_zero(store, sessionmaker):
    assert await finalize_session(store, sessionmaker, str(uuid.uuid4()), "completed") == (0, None)


async def test_db_failure_keeps_redis_reps_and_logs(store, caplog):
    sid, _ = await _seed(store, [100, 90, 80])
    engine, bad = create_engine_and_sessionmaker(
        Settings(database_url="postgresql+asyncpg://postgres:postgres@localhost:5499/none")
    )
    with caplog.at_level(logging.ERROR):
        with pytest.raises(Exception):
            await finalize_session(store, bad, sid, "completed", retries=2, retry_delay=0)
    await engine.dispose()
    assert len(await store.get_reps(sid)) == 3
    assert sid in caplog.text


async def test_failed_transaction_leaves_no_partial_rows(store, sessionmaker):
    sid, _ = await _seed(store, [100, 999])  # 999 violates CHECK (score BETWEEN 0 AND 100)
    with pytest.raises(Exception):
        await finalize_session(store, sessionmaker, sid, "completed", retries=1, retry_delay=0)
    assert await _count(sessionmaker, "workout_sessions") == 0
    assert await _count(sessionmaker, "reps") == 0
    assert len(await store.get_reps(sid)) == 2


async def test_two_sessions_same_client_do_not_mix_reps(store, sessionmaker):
    a, cid = await _seed(store, [100, 100])
    b, _ = await _seed(store, [50], client_id=cid)
    await finalize_session(store, sessionmaker, a, "completed")
    await finalize_session(store, sessionmaker, b, "completed")
    assert await _count(sessionmaker, "reps", "WHERE session_id = :s", s=uuid.UUID(a)) == 2
    assert await _count(sessionmaker, "reps", "WHERE session_id = :s", s=uuid.UUID(b)) == 1
    assert await _count(sessionmaker, "clients") == 1
