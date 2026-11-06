"""PostgreSQL access for finished sessions. Raw SQL via SQLAlchemy Core text()."""
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def upsert_client(db: AsyncSession, client_id: str, nickname: str | None) -> None:
    await db.execute(
        text(
            "INSERT INTO clients (id, nickname) VALUES (CAST(:id AS uuid), :nickname) "
            "ON CONFLICT (id) DO UPDATE SET nickname = COALESCE(EXCLUDED.nickname, clients.nickname)"
        ),
        {"id": client_id, "nickname": nickname},
    )


async def save_session(
    db: AsyncSession,
    session_id: str,
    client_id: str,
    exercise: str,
    started_at: datetime,
    ended_at: datetime,
    status: str,
    reps: list[dict],
) -> tuple[int, float | None]:
    """Insert the session and all reps. The caller owns the transaction (commit/rollback)."""
    total = len(reps)
    avg = round(sum(r["score"] for r in reps) / total, 1) if total else None
    await db.execute(
        text(
            "INSERT INTO workout_sessions "
            "(id, client_id, exercise_type, started_at, ended_at, total_reps, avg_score, status) "
            "VALUES (CAST(:id AS uuid), CAST(:client_id AS uuid), :exercise, :started_at, :ended_at, "
            ":total, :avg, :status)"
        ),
        {"id": session_id, "client_id": client_id, "exercise": exercise, "started_at": started_at,
         "ended_at": ended_at, "total": total, "avg": avg, "status": status},
    )
    if reps:
        await db.execute(
            text(
                "INSERT INTO reps (session_id, rep_no, min_knee_angle, max_trunk_tilt, score, "
                "feedback_codes, performed_at) VALUES (CAST(:session_id AS uuid), :rep_no, "
                ":min_knee_angle, :max_trunk_tilt, :score, :feedback_codes, :performed_at)"
            ),
            [
                {
                    "session_id": session_id,
                    "rep_no": r["rep_no"],
                    "min_knee_angle": r["min_knee_angle"],
                    "max_trunk_tilt": r["max_trunk_tilt"],
                    "score": r["score"],
                    "feedback_codes": list(r["feedback_codes"]),
                    "performed_at": datetime.fromisoformat(r["performed_at"]),
                }
                for r in reps
            ],
        )
    return total, avg


# ---------- read side (REST) ----------

async def list_sessions(db: AsyncSession, client_id: str, limit: int, offset: int) -> tuple[list[dict], int]:
    rows = (await db.execute(
        text(
            "SELECT * FROM ("
            "  SELECT id::text AS id, started_at, ended_at, total_reps, avg_score::float AS avg_score, status,"
            "         (avg_score - lag(avg_score) OVER (ORDER BY started_at))::float AS score_delta"
            "  FROM workout_sessions WHERE client_id = CAST(:cid AS uuid)"
            ") s ORDER BY started_at DESC LIMIT :limit OFFSET :offset"
        ),
        {"cid": client_id, "limit": limit, "offset": offset},
    )).mappings().all()
    total = (await db.execute(
        text("SELECT count(*) FROM workout_sessions WHERE client_id = CAST(:cid AS uuid)"), {"cid": client_id}
    )).scalar_one()
    return [dict(r) for r in rows], total


async def get_session(db: AsyncSession, session_id: str) -> dict | None:
    row = (await db.execute(
        text(
            "SELECT id::text AS id, client_id::text AS client_id, exercise_type, started_at, ended_at,"
            " total_reps, avg_score::float AS avg_score, status"
            " FROM workout_sessions WHERE id = CAST(:sid AS uuid)"
        ),
        {"sid": session_id},
    )).mappings().first()
    if row is None:
        return None
    reps = (await db.execute(
        text(
            "SELECT rep_no, min_knee_angle, max_trunk_tilt, score, feedback_codes, performed_at"
            " FROM reps WHERE session_id = CAST(:sid AS uuid) ORDER BY rep_no"
        ),
        {"sid": session_id},
    )).mappings().all()
    return {**dict(row), "reps": [dict(r) for r in reps]}


async def stats_summary(db: AsyncSession, client_id: str, tz: str) -> dict:
    totals = (await db.execute(
        text(
            "SELECT count(DISTINCT s.id) AS total_sessions, count(r.id) AS total_reps,"
            " round(avg(r.score), 1)::float AS avg_score"
            " FROM workout_sessions s LEFT JOIN reps r ON r.session_id = s.id"
            " WHERE s.client_id = CAST(:cid AS uuid)"
        ),
        {"cid": client_id},
    )).mappings().one()
    weekly = (await db.execute(
        text(
            "SELECT to_char(date_trunc('week', s.started_at AT TIME ZONE :tz), 'YYYY-MM-DD') AS week,"
            " count(r.id) AS reps, round(avg(r.score), 1)::float AS avg_score"
            " FROM workout_sessions s JOIN reps r ON r.session_id = s.id"
            " WHERE s.client_id = CAST(:cid AS uuid) GROUP BY 1 ORDER BY 1"
        ),
        {"cid": client_id, "tz": tz},
    )).mappings().all()
    return {**dict(totals), "weekly": [dict(w) for w in weekly]}


async def stats_mistakes(db: AsyncSession, client_id: str) -> list[dict]:
    rows = (await db.execute(
        text(
            "SELECT code, count(*) AS count FROM ("
            "  SELECT unnest(r.feedback_codes) AS code FROM reps r"
            "  JOIN workout_sessions s ON s.id = r.session_id WHERE s.client_id = CAST(:cid AS uuid)"
            ") c GROUP BY code ORDER BY count DESC, code"
        ),
        {"cid": client_id},
    )).mappings().all()
    return [dict(r) for r in rows]
