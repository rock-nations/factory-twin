import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request

from app.core.neo4j import get_async_driver, get_sync_driver
from app.models.factory import AlarmOut, GraphView, MachineDetail, MachineSummary, PlantSummary
from app.retrieval.machine_context import get_machine_context
from app.services import factory as svc
from app.services.factory import sensor_reading

router = APIRouter(prefix="/api", tags=["factory"])

MachineId = Annotated[str, Path(pattern=r"^M\d{3}$", examples=["M017"])]


@router.get("/plants", response_model=list[PlantSummary])
async def plants() -> list[PlantSummary]:
    return await svc.list_plants(get_async_driver())


@router.get("/machines", response_model=list[MachineSummary])
async def machines(
    line_id: str | None = Query(default=None, pattern=r"^[AB][1-4]$"),
    status: Literal["running", "warning", "alarm", "down"] | None = None,
) -> list[MachineSummary]:
    return await svc.list_machines(get_async_driver(), line_id, status)


@router.get("/machines/{machine_id}", response_model=MachineDetail)
async def machine_detail(machine_id: MachineId) -> MachineDetail:
    ctx = await asyncio.to_thread(get_machine_context, get_sync_driver(), machine_id, 10)
    if "error" in ctx:
        raise HTTPException(404, ctx["error"])
    ctx["sensors"] = [sensor_reading(s | {"as_of": s.get("as_of")}) for s in ctx["sensors"]]
    return MachineDetail(**ctx)


@router.get("/machines/{machine_id}/telemetry")
async def machine_telemetry(
    machine_id: MachineId, request: Request
) -> dict[str, list[dict[str, float | str]]]:
    """Last readings per sensor from the in-memory ring buffer (empty until the simulator runs)."""
    service = getattr(request.app.state, "streaming", None)
    if service is None:
        raise HTTPException(503, "streaming is disabled (ENABLE_STREAMING=0)")
    series: dict[str, list[dict[str, float | str]]] = service.buffer.series(machine_id)
    return series


@router.get("/graph/neighborhood", response_model=GraphView)
async def graph_neighborhood(
    node_id: str = Query(min_length=1, max_length=120), depth: int = Query(default=1, ge=1, le=2)
) -> GraphView:
    view = await svc.neighborhood(get_async_driver(), node_id, depth)
    if view is None:
        raise HTTPException(404, f"node '{node_id}' not found")
    return view


@router.get("/alarms", response_model=list[AlarmOut])
async def alarms(
    status: Literal["open", "acknowledged", "resolved"] | None = "open",
    machine_id: str | None = Query(default=None, pattern=r"^M\d{3}$"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AlarmOut]:
    return await svc.list_alarms(get_async_driver(), status, machine_id, limit)


@router.post("/alarms/{alarm_id}/ack")
async def acknowledge(alarm_id: str, request: Request) -> dict[str, str]:
    status = await svc.acknowledge_alarm(get_async_driver(), alarm_id)
    if status is None:
        raise HTTPException(404, f"alarm '{alarm_id}' not found")
    hub = getattr(request.app.state, "events_hub", None)
    if hub is not None:
        await hub.broadcast({"type": "alarm_acknowledged", "alarm_id": alarm_id, "status": status})
    return {"id": alarm_id, "status": status}
