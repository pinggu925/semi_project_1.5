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
