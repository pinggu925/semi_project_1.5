import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app.config import Settings, get_settings
from app.analyzer.thresholds import load_thresholds
from app.db import create_engine_and_sessionmaker
from app.ws import router as ws_router

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine, maker = create_engine_and_sessionmaker(settings)
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        app.state.settings = settings
        app.state.sessionmaker = maker
        app.state.redis = redis
        app.state.thresholds = load_thresholds(settings.thresholds_path)
        if getattr(app.state, "landmark_source_factory", None) is None:
            from app.pose import MediaPipeLandmarkSource

            app.state.landmark_source_factory = MediaPipeLandmarkSource
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(title="Squat Coach", lifespan=lifespan)
    app.include_router(ws_router)

    @app.get("/health")
    async def health():
        status = {"postgres": "ok", "redis": "ok"}
        try:
            async with app.state.sessionmaker() as db:
                await db.execute(text("SELECT 1"))
        except Exception:
            log.exception("postgres health check failed")
            status["postgres"] = "down"
        try:
            await app.state.redis.ping()
        except Exception:
            log.warning("redis health check failed")
            status["redis"] = "down"
        code = 200 if all(v == "ok" for v in status.values()) else 503
        return JSONResponse(status, status_code=code)

    return app
