"""One Neo4j driver per process. The API uses the async driver; scripts may use the sync one."""

import logging

from neo4j import AsyncDriver, AsyncGraphDatabase, Driver, GraphDatabase

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)
# server hints like "index already exists" / "property does not exist" are noise in logs
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

_async_driver: AsyncDriver | None = None
_sync_driver: Driver | None = None


def _auth(settings: Settings) -> tuple[str, str]:
    return (settings.neo4j_user, settings.neo4j_password)


async def init_async_driver(settings: Settings | None = None) -> AsyncDriver:
    global _async_driver
    if _async_driver is None:
        s = settings or get_settings()
        _async_driver = AsyncGraphDatabase.driver(s.neo4j_uri, auth=_auth(s))
        logger.info("Neo4j async driver created for %s", s.neo4j_uri)
    return _async_driver


def get_async_driver() -> AsyncDriver:
    if _async_driver is None:
        raise RuntimeError("Neo4j driver not initialised; call init_async_driver() first")
    return _async_driver


async def close_async_driver() -> None:
    global _async_driver
    if _async_driver is not None:
        await _async_driver.close()
        _async_driver = None


def get_sync_driver() -> Driver:
    """Process-wide sync driver for the retrieval layer (neo4j-graphrag retrievers are sync).

    Callers in async code run retrieval via ``asyncio.to_thread``.
    """
    global _sync_driver
    if _sync_driver is None:
        _sync_driver = create_sync_driver()
    return _sync_driver


def close_sync_driver() -> None:
    global _sync_driver
    if _sync_driver is not None:
        _sync_driver.close()
        _sync_driver = None


def create_sync_driver(settings: Settings | None = None) -> Driver:
    """For ingestion scripts and CLIs (one per process; caller closes it)."""
    s = settings or get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=_auth(s))
