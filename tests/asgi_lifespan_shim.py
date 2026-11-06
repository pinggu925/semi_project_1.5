from contextlib import asynccontextmanager

import httpx


@asynccontextmanager
async def lifespan_client(app):
    """httpx client that also runs the FastAPI lifespan."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
