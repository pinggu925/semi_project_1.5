"""Live (volatile) session state in Redis. Finished sessions live in PostgreSQL."""
import json
from datetime import datetime

from redis.asyncio import Redis

from app.analyzer.types import RepResult

ANGLE_WINDOW = 5


class SessionStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 3600):
        self.r = redis
        self.ttl = ttl_seconds

    @staticmethod
    def _keys(sid: str) -> tuple[str, str, str]:
        return f"session:{sid}", f"session:{sid}:angles", f"session:{sid}:reps"

    def _expire_all(self, pipe, sid: str) -> None:
        for key in self._keys(sid):
            pipe.expire(key, self.ttl)

    async def create(self, session_id: str, client_id: str, exercise: str, started_at: datetime) -> None:
        h, _, _ = self._keys(session_id)
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.hset(h, mapping={
                "client_id": client_id, "exercise": exercise, "phase": "STANDING",
                "rep_count": 0, "started_at": started_at.isoformat(), "current_min_angle": "",
            })
            self._expire_all(pipe, session_id)
            await pipe.execute()

    async def update_state(self, session_id: str, phase: str, rep_count: int, current_min_angle: float | None) -> None:
        h, _, _ = self._keys(session_id)
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.hset(h, mapping={
                "phase": phase, "rep_count": rep_count,
                "current_min_angle": "" if current_min_angle is None else round(current_min_angle, 1),
            })
            self._expire_all(pipe, session_id)
            await pipe.execute()

    async def push_angle(self, session_id: str, angle: float) -> None:
        _, a, _ = self._keys(session_id)
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.lpush(a, round(angle, 1))
            pipe.ltrim(a, 0, ANGLE_WINDOW - 1)
            self._expire_all(pipe, session_id)
            await pipe.execute()

    async def append_rep(self, session_id: str, rep: RepResult, performed_at: datetime) -> None:
        _, _, reps = self._keys(session_id)
        payload = json.dumps({
            "rep_no": rep.rep_no, "min_knee_angle": rep.min_knee_angle,
            "max_trunk_tilt": rep.max_trunk_tilt, "score": rep.score,
            "feedback_codes": list(rep.feedback_codes), "performed_at": performed_at.isoformat(),
        })
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.rpush(reps, payload)
            self._expire_all(pipe, session_id)
            await pipe.execute()

    async def get_state(self, session_id: str) -> dict[str, str] | None:
        state = await self.r.hgetall(self._keys(session_id)[0])
        return state or None

    async def get_reps(self, session_id: str) -> list[dict]:
        return [json.loads(x) for x in await self.r.lrange(self._keys(session_id)[2], 0, -1)]

    async def delete(self, session_id: str) -> None:
        await self.r.delete(*self._keys(session_id))
