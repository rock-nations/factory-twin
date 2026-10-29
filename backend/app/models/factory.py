"""REST response models for the factory dashboard (spec §9)."""

from typing import Any, Literal

from pydantic import BaseModel, Field

MachineStatus = Literal["running", "warning", "alarm", "down"]
SensorState = Literal["normal", "warning", "critical", "no_data"]
STATUSES: tuple[MachineStatus, ...] = ("running", "warning", "alarm", "down")


class StatusCounts(BaseModel):
    running: int = 0
    warning: int = 0
    alarm: int = 0
    down: int = 0

    @property
    def total(self) -> int:
        return self.running + self.warning + self.alarm + self.down


class LineSummary(BaseModel):
    id: str
    name: str
    machine_count: int
    status_counts: StatusCounts


class PlantSummary(BaseModel):
    id: str
    name: str
    location: str | None = None
    machine_count: int
    status_counts: StatusCounts
    lines: list[LineSummary]


class SensorReading(BaseModel):
    sensor: str
    unit: str
    value: float | None = None
    as_of: str | None = None
    avg_24h: float | None = None
    warn_high: float | None = None
    crit_high: float | None = None
    warn_low: float | None = None
    crit_low: float | None = None
    state: SensorState = "no_data"


class MachineSummary(BaseModel):
    id: str
    machine_id: int
    age: int
    model: str
    line_id: str
    plant_id: str
    position: int
    status: MachineStatus
    sensors: list[SensorReading] = Field(default_factory=list)


class AlarmOut(BaseModel):
    id: str
    machine_id: str
    sensor_type: str | None = None
    severity: Literal["warning", "critical"]
    value: float | None = None
    threshold: float | None = None
    status: Literal["open", "acknowledged", "resolved"]
    message: str | None = None
    ts: str | None = None
    similar_failures: list[dict[str, Any]] = Field(default_factory=list)


class MachineDetail(BaseModel):
    machine: dict[str, Any]
    sensors: list[SensorReading]
    open_alarms: list[dict[str, Any]]
    components: list[dict[str, Any]]
    recent_maintenance: list[dict[str, Any]]
    downstream: list[dict[str, Any]]
    upstream: list[dict[str, Any]]


class GraphNode(BaseModel):
    id: str
    label: str
    caption: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str


class GraphView(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = False
