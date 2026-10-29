from typing import Literal

from pydantic import BaseModel

ComponentStatus = Literal["ok", "error"]


class ComponentHealth(BaseModel):
    status: ComponentStatus
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    neo4j: ComponentHealth
    mqtt: ComponentHealth
