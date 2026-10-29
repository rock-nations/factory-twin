import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, factory, health, sim
from app.chat.service import get_hub
from app.core.config import get_settings
from app.core.neo4j import close_async_driver, close_sync_driver, init_async_driver
from app.streaming.consumer import StreamingService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# "property/relationship does not exist" hints are noise before the first alarm is raised
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    driver = await init_async_driver()
    settings = get_settings()
    streaming: StreamingService | None = None
    if settings.enable_streaming:
        streaming = StreamingService(driver, sim.events_hub(app), get_hub(app), settings)
        app.state.streaming = streaming
        await streaming.start()
    try:
        yield
    finally:
        if streaming is not None:
            await streaming.stop()
        await close_async_driver()
        close_sync_driver()


def create_app() -> FastAPI:
    app = FastAPI(title="FactoryTwin Copilot", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(sim.router)
    app.include_router(factory.router)
    return app


app = create_app()
