from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase, Driver

from app.api import factory as factory_api
from app.chat.hub import ChatHub
from app.core.config import Settings
from app.ingestion.build_topology import build_topology
from app.ingestion.common import run_write
from app.ingestion.compute_thresholds import compute_and_store_thresholds
from app.ingestion.init_schema import init_schema
from app.ingestion.load_events import load_events
from app.main import create_app
from app.streaming.consumer import StreamingService
from tests.conftest import TEST_MQTT_PORT, TEST_NEO4J_PASSWORD, TEST_NEO4J_URI

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def api(
    clean_db: Driver,
    pdm_dir: Path,
    test_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    init_schema(clean_db, test_settings)
    build_topology(clean_db, pdm_dir)
    load_events(clean_db, pdm_dir)
    compute_and_store_thresholds(clean_db, pdm_dir, tmp_path)
    run_write(
        clean_db,
        """
        MATCH (m:Machine {id: 'M003'})-[:HAS_SENSOR]->(s:Sensor {type: 'vibration'})
        SET m.status = 'alarm', s.latest_value = 999.0, s.latest_ts = datetime()
        WITH m
        CREATE (a:Alarm {id: 'AL-1', status: 'open', severity: 'critical', sensor_type: 'vibration',
                         value: 999.0, threshold: 57.5, ts: datetime(), message: 'high',
                         source: 'simulator'})
        CREATE (a)-[:RAISED_ON]->(m)
        WITH m
        MATCH (m2:Machine {id: 'M004'}) SET m2.status = 'warning'
        """,
    )
    adriver = AsyncGraphDatabase.driver(TEST_NEO4J_URI, auth=("neo4j", TEST_NEO4J_PASSWORD))
    monkeypatch.setattr(factory_api, "get_async_driver", lambda: adriver)
    monkeypatch.setattr(factory_api, "get_sync_driver", lambda: clean_db)
    app = create_app()
    app.state.streaming = StreamingService(
        adriver,
        factory_api_events(app),
        ChatHub(),
        Settings(_env_file=None, mqtt_port=TEST_MQTT_PORT),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        yield c
    await adriver.close()


def factory_api_events(app: Any) -> Any:
    from app.api.sim import events_hub

    return events_hub(app)


async def test_plants_summary_with_status_counts(api: httpx.AsyncClient) -> None:
    plants = (await api.get("/api/plants")).json()
    assert [p["id"] for p in plants] == ["P1", "P2"]
    assert plants[0]["name"] == "Munich Assembly" and len(plants[0]["lines"]) == 4
    assert sum(p["machine_count"] for p in plants) == 100
    p1 = plants[0]["status_counts"]
    assert (
        p1["alarm"] == 1 and p1["warning"] == 1 and p1["running"] == plants[0]["machine_count"] - 2
    )
    a1 = plants[0]["lines"][0]
    assert a1["id"] == "A1" and a1["machine_count"] == 13 and a1["status_counts"]["alarm"] == 1


async def test_machines_filters_and_live_sensor_state(api: httpx.AsyncClient) -> None:
    every = (await api.get("/api/machines")).json()
    assert len(every) == 100 and every[0]["id"] == "M001" and every[0]["position"] == 1
    a1 = (await api.get("/api/machines", params={"line_id": "A1"})).json()
    assert len(a1) == 13 and {m["line_id"] for m in a1} == {"A1"}
    alarm = (await api.get("/api/machines", params={"status": "alarm"})).json()
    assert [m["id"] for m in alarm] == ["M003"]
    sensors = {s["sensor"]: s for s in alarm[0]["sensors"]}
    assert list(sensors) == ["volt", "rotate", "pressure", "vibration"]
    assert sensors["vibration"]["state"] == "critical" and sensors["vibration"]["value"] == 999.0
    assert sensors["volt"]["state"] == "no_data" and sensors["volt"]["warn_high"] is not None


async def test_machines_validates_filters(api: httpx.AsyncClient) -> None:
    assert (await api.get("/api/machines", params={"status": "exploded"})).status_code == 422
    assert (await api.get("/api/machines", params={"line_id": "Z9"})).status_code == 422


async def test_machine_detail(api: httpx.AsyncClient) -> None:
    body = (await api.get("/api/machines/M003")).json()
    assert body["machine"]["id"] == "M003" and body["machine"]["line"] == "A1"
    assert len(body["components"]) == 4 and len(body["sensors"]) == 4
    assert body["open_alarms"][0]["alarm"] == "AL-1"
    assert {d["machine"] for d in body["downstream"]} >= {"M004"}
    assert (await api.get("/api/machines/M999")).status_code == 404
    assert (await api.get("/api/machines/abc")).status_code == 422


async def test_detail_includes_up_to_ten_maintenance_events(api: httpx.AsyncClient) -> None:
    busiest = (
        await api.get("/api/machines", params={"line_id": "A1"})
    ).json()  # any machine works; just verify the key exists and is bounded
    body = (await api.get(f"/api/machines/{busiest[0]['id']}")).json()
    assert len(body["recent_maintenance"]) <= 10


async def test_alarms_list_and_ack(api: httpx.AsyncClient) -> None:
    open_alarms = (await api.get("/api/alarms")).json()
    assert [a["id"] for a in open_alarms] == ["AL-1"] and open_alarms[0]["machine_id"] == "M003"
    assert (await api.get("/api/alarms", params={"status": "resolved"})).json() == []
    ack = await api.post("/api/alarms/AL-1/ack")
    assert ack.json() == {"id": "AL-1", "status": "acknowledged"}
    assert (await api.get("/api/alarms")).json() == []
    assert [
        a["status"]
        for a in (await api.get("/api/alarms", params={"status": "acknowledged"})).json()
    ] == ["acknowledged"]
    assert (await api.post("/api/alarms/nope/ack")).status_code == 404


async def test_telemetry_series_comes_from_the_ring_buffer(api: httpx.AsyncClient) -> None:
    body = (await api.get("/api/machines/M003/telemetry")).json()
    assert set(body) == {"volt", "rotate", "pressure", "vibration"} and body["volt"] == []


async def test_neighborhood_depth_one(api: httpx.AsyncClient) -> None:
    g = (await api.get("/api/graph/neighborhood", params={"node_id": "M003"})).json()
    by_label: dict[str, list[str]] = {}
    for n in g["nodes"]:
        by_label.setdefault(n["label"], []).append(n["id"])
    assert "M003" in by_label["Machine"] and "M004" in by_label["Machine"]
    assert set(by_label["Sensor"]) == {
        f"M003-{s}" for s in ("volt", "rotate", "pressure", "vibration")
    }
    assert "A1" in by_label["ProductionLine"] and "model4" in by_label["MachineModel"]
    assert "Alarm" in by_label and "ErrorEvent" not in by_label  # noisy labels are left out
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])
    types = {e["type"] for e in g["edges"]}
    assert {"HAS_SENSOR", "IS_MODEL", "FEEDS_INTO", "RAISED_ON"} <= types
    node = next(n for n in g["nodes"] if n["id"] == "M003")
    assert node["caption"] == "M003" and node["properties"]["status"] == "alarm"


async def test_neighborhood_depth_two_reaches_further_but_stays_bounded(
    api: httpx.AsyncClient,
) -> None:
    d1 = (await api.get("/api/graph/neighborhood", params={"node_id": "M003"})).json()
    d2 = (await api.get("/api/graph/neighborhood", params={"node_id": "M003", "depth": 2})).json()
    assert len(d2["nodes"]) > len(d1["nodes"]) and len(d2["nodes"]) <= 150
    assert len({n["id"] for n in d2["nodes"]}) == len(d2["nodes"])  # no duplicates
    # hub nodes are shown but not expanded: the model node does not drag in all of its machines
    machines = [n for n in d2["nodes"] if n["label"] == "Machine"]
    assert len(machines) < 40


async def test_neighborhood_errors(api: httpx.AsyncClient) -> None:
    assert (await api.get("/api/graph/neighborhood", params={"node_id": "ZZZ"})).status_code == 404
    assert (
        await api.get("/api/graph/neighborhood", params={"node_id": "M003", "depth": 5})
    ).status_code == 422
