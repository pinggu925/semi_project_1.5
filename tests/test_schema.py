import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration
NOW = datetime.now(timezone.utc)


async def _client_and_session(db, exercise="squat"):
    cid, sid = uuid.uuid4(), uuid.uuid4()
    await db.execute(text("INSERT INTO clients (id) VALUES (:id)"), {"id": cid})
    await db.execute(
        text(
            "INSERT INTO workout_sessions (id, client_id, exercise_type, started_at, status) "
            "VALUES (:id, :cid, :ex, :t, 'completed')"
        ),
        {"id": sid, "cid": cid, "ex": exercise, "t": NOW},
    )
    return sid


async def _rep(db, sid, rep_no):
    await db.execute(
        text(
            "INSERT INTO reps (session_id, rep_no, min_knee_angle, max_trunk_tilt, score, performed_at) "
            "VALUES (:sid, :n, 90, 10, 100, :t)"
        ),
        {"sid": sid, "n": rep_no, "t": NOW},
    )


async def test_schema_rejects_unsupported_exercise_type(clean_db, sessionmaker):
    async with sessionmaker() as db:
        with pytest.raises(IntegrityError):
            await _client_and_session(db, exercise="pushup")


async def test_reps_unique_per_session_and_rep_no(clean_db, sessionmaker):
    async with sessionmaker() as db:
        sid = await _client_and_session(db)
        await _rep(db, sid, 1)
        with pytest.raises(IntegrityError):
            await _rep(db, sid, 1)


async def test_deleting_session_cascades_to_reps(clean_db, sessionmaker):
    async with sessionmaker() as db:
        sid = await _client_and_session(db)
        await _rep(db, sid, 1)
        await db.execute(text("DELETE FROM workout_sessions WHERE id = :id"), {"id": sid})
        assert (await db.execute(text("SELECT count(*) FROM reps"))).scalar() == 0
