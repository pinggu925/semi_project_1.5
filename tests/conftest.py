import os
from pathlib import Path

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from app.config import Settings
from app.db import create_engine_and_sessionmaker

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/squatcoach"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url=TEST_DB_URL, redis_url=TEST_REDIS_URL)


@pytest.fixture
async def sessionmaker(settings):
    engine, maker = create_engine_and_sessionmaker(settings)
    yield maker
    await engine.dispose()


@pytest.fixture
async def redis_client(settings):
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def clean_db(sessionmaker, redis_client):
    async with sessionmaker() as db:
        await db.execute(text("TRUNCATE reps, workout_sessions, clients CASCADE"))
        await db.commit()
    await redis_client.flushdb()
    yield
