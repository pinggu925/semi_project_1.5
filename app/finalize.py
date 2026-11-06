"""Move a finished live session from Redis into PostgreSQL."""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.redis_store import SessionStore
from app.repository import save_session, upsert_client

log = logging.getLogger(__name__)


async def finalize_session(
    store: SessionStore,
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: str,
    status: str,
    retries: int = 3,
    retry_delay: float = 0.5,
) -> tuple[int, float | None]:
    state = await store.get_state(session_id)
    reps = await store.get_reps(session_id)
    if state is None or not reps:
        await store.delete(session_id)
        return 0, None

    started_at = datetime.fromisoformat(state["started_at"])
    ended_at = datetime.now(timezone.utc)
    last_error: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            async with sessionmaker() as db:
                async with db.begin():
                    await upsert_client(db, state["client_id"], None)
                    result = await save_session(
                        db, session_id, state["client_id"], state["exercise"],
                        started_at, ended_at, status, reps,
                    )
            await store.delete(session_id)
            return result
        except Exception as exc:  # noqa: BLE001 - any DB failure is retried, then surfaced
            last_error = exc
            log.warning("finalize attempt %d failed for session %s: %s", attempt, session_id, exc)
            if attempt < retries and retry_delay:
                await asyncio.sleep(retry_delay)
    log.error("finalize failed for session %s; reps kept in Redis: %s", session_id, last_error)
    assert last_error is not None
    raise last_error
