import asyncio
import logging

import aiomqtt
from fastapi import APIRouter

from app.core.config import get_settings
from app.core.neo4j import get_async_driver
from app.models.health import ComponentHealth, HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["health"])

CHECK_TIMEOUT_S = 3.0


async def _check_neo4j() -> ComponentHealth:
    try:
        await asyncio.wait_for(get_async_driver().verify_connectivity(), CHECK_TIMEOUT_S)
        return ComponentHealth(status="ok")
    except Exception as exc:
        logger.warning("Neo4j health check failed: %s", exc)
        return ComponentHealth(status="error", detail=str(exc) or type(exc).__name__)


async def _check_mqtt() -> ComponentHealth:
    s = get_settings()
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_S):
            async with aiomqtt.Client(s.mqtt_host, s.mqtt_port):
                pass
        return ComponentHealth(status="ok")
    except Exception as exc:
        logger.warning("MQTT health check failed: %s", exc)
        return ComponentHealth(status="error", detail=str(exc) or type(exc).__name__)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    neo4j, mqtt = await asyncio.gather(_check_neo4j(), _check_mqtt())
    ok = neo4j.status == "ok" and mqtt.status == "ok"
    return HealthResponse(status="ok" if ok else "degraded", neo4j=neo4j, mqtt=mqtt)
