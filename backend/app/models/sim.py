from typing import Literal

from pydantic import BaseModel, Field

from app.streaming.messages import SimStatus

SensorName = Literal["volt", "rotate", "pressure", "vibration"]


class SimStatusResponse(SimStatus):
    mqtt_connected: bool = False


class SpeedRequest(BaseModel):
    speed: float = Field(gt=0, le=100, description="simulated hours per real second")


class InjectRequest(BaseModel):
    machine_id: str = Field(pattern=r"^M\d{3}$")
    sensor: SensorName
    magnitude: float = Field(default=4.0, ge=-10, le=10, description="σ multiples")
    duration_s: float = Field(default=60.0, gt=0, le=3600)


class SimAck(BaseModel):
    ok: bool = True
    detail: str | None = None


class ResetResponse(BaseModel):
    alarms_deleted: int
    simulator_errors_deleted: int
    machines_reset: int
    sensors_cleared: int
