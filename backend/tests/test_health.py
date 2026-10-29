from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import health
from app.main import create_app
from app.models.health import ComponentHealth


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def test_health_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok() -> ComponentHealth:
        return ComponentHealth(status="ok")

    monkeypatch.setattr(health, "_check_neo4j", ok)
    monkeypatch.setattr(health, "_check_mqtt", ok)
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["neo4j"]["status"] == "ok"
    assert body["mqtt"]["status"] == "ok"


def test_health_degraded_when_a_dependency_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok() -> ComponentHealth:
        return ComponentHealth(status="ok")

    async def down() -> ComponentHealth:
        return ComponentHealth(status="error", detail="connection refused")

    monkeypatch.setattr(health, "_check_neo4j", down)
    monkeypatch.setattr(health, "_check_mqtt", ok)
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert body["neo4j"]["detail"] == "connection refused"


def test_cors_allows_angular_dev_origin(client: TestClient) -> None:
    r = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:4200",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.headers["access-control-allow-origin"] == "http://localhost:4200"
