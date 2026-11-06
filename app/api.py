"""REST API for workout history and statistics."""
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app import repository

router = APIRouter(prefix="/api")


class SessionItem(BaseModel):
    id: str
    started_at: datetime
    ended_at: datetime | None
    total_reps: int
    avg_score: float | None
    status: str
    score_delta: float | None


class SessionList(BaseModel):
    items: list[SessionItem]
    total: int


class RepOut(BaseModel):
    rep_no: int
    min_knee_angle: float
    max_trunk_tilt: float
    score: int
    feedback_codes: list[str]
    performed_at: datetime


class SessionDetail(BaseModel):
    id: str
    client_id: str
    exercise_type: str
    started_at: datetime
    ended_at: datetime | None
    total_reps: int
    avg_score: float | None
    status: str
    reps: list[RepOut]


class WeeklyPoint(BaseModel):
    week: str
    reps: int
    avg_score: float | None


class Summary(BaseModel):
    total_sessions: int
    total_reps: int
    avg_score: float | None
    weekly: list[WeeklyPoint]


class Mistake(BaseModel):
    code: str
    count: int


def _db(request: Request):
    return request.app.state.sessionmaker()


@router.get("/sessions", response_model=SessionList)
async def sessions(request: Request, client_id: uuid.UUID,
                   limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    async with _db(request) as db:
        items, total = await repository.list_sessions(db, str(client_id), limit, offset)
    return {"items": items, "total": total}


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def session_detail(request: Request, session_id: uuid.UUID):
    async with _db(request) as db:
        detail = await repository.get_session(db, str(session_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="session not found")
    return detail


@router.get("/stats/summary", response_model=Summary)
async def summary(request: Request, client_id: uuid.UUID):
    async with _db(request) as db:
        return await repository.stats_summary(db, str(client_id), request.app.state.settings.stats_timezone)


@router.get("/stats/mistakes", response_model=list[Mistake])
async def mistakes(request: Request, client_id: uuid.UUID):
    async with _db(request) as db:
        return await repository.stats_mistakes(db, str(client_id))
