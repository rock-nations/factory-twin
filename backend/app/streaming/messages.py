"""MQTT topics and payloads shared by the simulator, the consumer and the control API."""

from typing import Literal

from pydantic import BaseModel, Field

from app.graph import constants as C

ROOT = "factory"
CONTROL_TOPIC = f"{ROOT}/control/sim"
STATUS_TOPIC = f"{ROOT}/control/sim/status"
SubscribeAll = f"{ROOT}/#"

SimState = Literal["running", "paused", "finished", "offline"]
Kind = Literal["telemetry", "errors"]


def telemetry_topic(plant: str, line: str, machine_id: str) -> str:
    return f"{ROOT}/{plant}/{line}/{machine_id}/telemetry"


def errors_topic(plant: str, line: str, machine_id: str) -> str:
    return f"{ROOT}/{plant}/{line}/{machine_id}/errors"


def parse_data_topic(topic: str) -> tuple[Kind, str, str, str] | None:
    """-> (kind, plant, line, machine_id) for factory/{plant}/{line}/{machine}/{kind}."""
    parts = topic.split("/")
    if len(parts) == 5 and parts[0] == ROOT and parts[4] in ("telemetry", "errors"):
        return parts[4], parts[1], parts[2], parts[3]  # type: ignore[return-value]
    return None


class TelemetryMsg(BaseModel):
    machine_id: str
    ts: str  # wall-clock time of publication (UTC, ISO)
    sim_ts: str | None = None  # original dataset timestamp
    volt: float
    rotate: float
    pressure: float
    vibration: float

    def readings(self) -> dict[str, float]:
        return {s: getattr(self, s) for s in C.SENSOR_UNITS}


class ErrorMsg(BaseModel):
    machine_id: str
    ts: str
    sim_ts: str | None = None
    error: str


class SimStatus(BaseModel):
    state: SimState = "offline"
    speed: float = 0.5
    sim_time: str | None = None
    loop: bool = False


class SimCommand(BaseModel):
    cmd: Literal["start", "stop", "speed", "inject"]
    speed: float | None = Field(default=None, gt=0, le=100)
    machine_id: str | None = None
    sensor: str | None = None
    magnitude: float | None = None  # in σ; negative pushes volt/pressure below normal
    duration_s: float | None = Field(default=None, gt=0, le=3600)
