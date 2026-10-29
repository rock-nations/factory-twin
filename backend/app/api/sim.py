import asyncio
import logging
from typing import Any

import aiomqtt
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.core.config import get_settings
from app.core.neo4j import get_sync_driver
from app.ingestion.reset_live_state import reset_live_state
from app.models.sim import (
    InjectRequest,
    ResetResponse,
    SimAck,
    SimStatusResponse,
    SpeedRequest,
)
from app.streaming import messages as M
from app.streaming.consumer import StreamingService
from app.streaming.control import publish_command
from app.streaming.events import EventsHub

logger = logging.getLogger(__name__)
router = APIRouter(tags=["simulator"])


def events_hub(app: Any) -> EventsHub:
    hub: EventsHub | None = getattr(app.state, "events_hub", None)
    if hub is None:
        hub = app.state.events_hub = EventsHub()
    return hub


def get_streaming(request: Request) -> StreamingService:
    service: StreamingService | None = getattr(request.app.state, "streaming", None)
    if service is None:
        raise HTTPException(503, "streaming is disabled (ENABLE_STREAMING=0)")
    return service


async def _send(command: M.SimCommand) -> SimAck:
    try:
        await publish_command(get_settings(), command)
    except (aiomqtt.MqttError, OSError) as exc:
        raise HTTPException(503, f"MQTT broker unreachable: {exc}") from exc
    return SimAck()


@router.get("/api/sim/status", response_model=SimStatusResponse)
async def sim_status(request: Request) -> SimStatusResponse:
    service = get_streaming(request)
    return SimStatusResponse(
        **service.sim_status.model_dump(), mqtt_connected=service.mqtt_connected
    )


@router.post("/api/sim/start", response_model=SimAck)
async def sim_start() -> SimAck:
    return await _send(M.SimCommand(cmd="start"))


@router.post("/api/sim/stop", response_model=SimAck)
async def sim_stop() -> SimAck:
    return await _send(M.SimCommand(cmd="stop"))


@router.post("/api/sim/speed", response_model=SimAck)
async def sim_speed(body: SpeedRequest) -> SimAck:
    return await _send(M.SimCommand(cmd="speed", speed=body.speed))


@router.post("/api/sim/inject", response_model=SimAck)
async def sim_inject(body: InjectRequest, request: Request) -> SimAck:
    service = get_streaming(request)
    if service.sim_status.state != "running":
        raise HTTPException(
            409, "the simulator is not running; start it before injecting an anomaly"
        )
    return await _send(
        M.SimCommand(
            cmd="inject",
            machine_id=body.machine_id,
            sensor=body.sensor,
            magnitude=body.magnitude,
            duration_s=body.duration_s,
        )
    )


@router.post("/api/sim/reset", response_model=ResetResponse)
async def sim_reset(request: Request) -> ResetResponse:
    service = get_streaming(request)
    if service.sim_status.state == "running":
        raise HTTPException(409, "stop the simulator before resetting the live state")
    summary = await asyncio.to_thread(reset_live_state, get_sync_driver())
    await service.reset()
    return ResetResponse(**summary)


@router.websocket("/ws/events")
async def events_socket(ws: WebSocket) -> None:
    await ws.accept()
    hub = events_hub(ws.app)
    hub.register(ws)
    service: StreamingService | None = getattr(ws.app.state, "streaming", None)
    try:
        if service is not None:  # new clients learn the current simulator state immediately
            await hub.send(ws, {"type": "sim_status", **service.sim_status.model_dump()})
        while True:
            await ws.receive_text()  # clients send nothing; this detects disconnects
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister(ws)
