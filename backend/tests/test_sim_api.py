import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import aiomqtt
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j import AsyncGraphDatabase, Driver

from app.api import sim
from app.chat.hub import ChatHub
from app.core.config import Settings
from app.ingestion.build_topology import build_topology
from app.ingestion.init_schema import init_schema
from app.main import create_app
from app.streaming import messages as M
from app.streaming.consumer import StreamingService
from app.streaming.events import EventsHub
from tests.conftest import TEST_MQTT_PORT, TEST_NEO4J_PASSWORD, TEST_NEO4J_URI
from tests.test_streaming_integration import broker_up

SETTINGS = Settings(_env_file=None, mqtt_port=TEST_MQTT_PORT)


def app_with(state: M.SimState) -> tuple[FastAPI, StreamingService]:
    app = create_app()
    driver = AsyncGraphDatabase.driver(TEST_NEO4J_URI, auth=("neo4j", TEST_NEO4J_PASSWORD))
    service = StreamingService(driver, sim.events_hub(app), ChatHub(), SETTINGS)
    service.sim_status = M.SimStatus(state=state, speed=2.0)
    app.state.streaming = service
    return app, service


@pytest_asyncio.fixture
async def client_running(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(sim, "get_settings", lambda: SETTINGS)
    app, _ = app_with("running")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        yield c


async def next_command(settings: Settings, trigger: Any) -> dict[str, Any]:
    async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port) as sub:
        await sub.subscribe(M.CONTROL_TOPIC, qos=1)
        await trigger()
        async with asyncio.timeout(5):
            async for msg in sub.messages:
                command: dict[str, Any] = json.loads(bytes(msg.payload))
                return command
    raise AssertionError("no command received")


needs_broker = pytest.mark.skipif(not broker_up(), reason="test MQTT broker not running")


@needs_broker
@pytest.mark.integration
@pytest.mark.parametrize(
    ("path", "body", "expected"),
    [
        ("/api/sim/start", None, {"cmd": "start"}),
        ("/api/sim/stop", None, {"cmd": "stop"}),
        ("/api/sim/speed", {"speed": 3.5}, {"cmd": "speed", "speed": 3.5}),
        (
            "/api/sim/inject",
            {"machine_id": "M017", "sensor": "vibration", "magnitude": 4, "duration_s": 30},
            {
                "cmd": "inject",
                "machine_id": "M017",
                "sensor": "vibration",
                "magnitude": 4.0,
                "duration_s": 30.0,
            },
        ),
    ],
)
async def test_commands_are_published_to_the_control_topic(
    client_running: httpx.AsyncClient, path: str, body: Any, expected: dict[str, Any]
) -> None:
    got = await next_command(SETTINGS, lambda: client_running.post(path, json=body))
    assert got == expected


@pytest.mark.parametrize(
    "body",
    [
        {"machine_id": "17", "sensor": "vibration"},
        {"machine_id": "M017", "sensor": "humidity"},
        {"machine_id": "M017", "sensor": "volt", "magnitude": 99},
    ],
)
async def test_inject_validates_input(
    client_running: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    assert (await client_running.post("/api/sim/inject", json=body)).status_code == 422


async def test_speed_must_be_positive(client_running: httpx.AsyncClient) -> None:
    assert (await client_running.post("/api/sim/speed", json={"speed": 0})).status_code == 422


async def test_inject_requires_a_running_simulator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sim, "get_settings", lambda: SETTINGS)
    app, _ = app_with("paused")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        r = await c.post("/api/sim/inject", json={"machine_id": "M017", "sensor": "vibration"})
    assert r.status_code == 409


async def test_reset_is_refused_while_running(client_running: httpx.AsyncClient) -> None:
    r = await client_running.post("/api/sim/reset")
    assert r.status_code == 409 and "stop the simulator" in r.json()["detail"]


@pytest.mark.integration
async def test_reset_runs_when_stopped(
    clean_db: Driver, test_settings: Settings, pdm_dir: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_schema(clean_db, test_settings)
    build_topology(clean_db, pdm_dir)
    clean_db.execute_query(
        "MATCH (m:Machine {id: 'M001'}) SET m.status = 'alarm' CREATE (:Alarm {id: 'x'})"
    )
    monkeypatch.setattr(sim, "get_sync_driver", lambda: clean_db)
    app, service = app_with("paused")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        r = await c.post("/api/sim/reset")
    assert r.status_code == 200
    assert r.json()["alarms_deleted"] == 1 and r.json()["machines_reset"] == 100
    status = clean_db.execute_query("MATCH (m:Machine {id: 'M001'}) RETURN m.status AS s").records[
        0
    ]["s"]
    assert status == "running"
    await service.driver.close()


async def test_status_endpoint() -> None:
    app, service = app_with("paused")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        body = (await c.get("/api/sim/status")).json()
    assert body == {
        "state": "paused",
        "speed": 2.0,
        "sim_time": None,
        "loop": False,
        "mqtt_connected": False,
    }
    await service.driver.close()


def test_endpoints_report_disabled_streaming() -> None:
    with TestClient(create_app()) as client:  # ENABLE_STREAMING=0 in tests
        assert client.get("/api/sim/status").status_code == 503


@pytest.mark.integration
def test_events_socket_sends_current_status_then_live_events() -> None:
    app, service = app_with("running")
    with TestClient(app) as client, client.websocket_connect("/ws/events") as ws:
        first = ws.receive_json()
        assert first == {
            "type": "sim_status",
            "state": "running",
            "speed": 2.0,
            "sim_time": None,
            "loop": False,
        }
        assert client.portal is not None
        client.portal.call(
            sim.events_hub(app).broadcast, {"type": "alarm_resolved", "alarm_id": "AL-1"}
        )
        assert ws.receive_json() == {"type": "alarm_resolved", "alarm_id": "AL-1"}
    assert len(sim.events_hub(app)) == 0


def test_events_hub_drops_dead_clients() -> None:
    hub = EventsHub()

    class Dead:
        async def send_json(self, _: Any) -> None:
            raise RuntimeError("closed")

    ws: Any = Dead()
    hub.register(ws)
    asyncio.run(hub.broadcast({"type": "x"}))
    assert len(hub) == 0
