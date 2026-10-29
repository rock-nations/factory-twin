"""Consumer + alarm engine + writer against the test Neo4j; end-to-end with the test broker."""

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from neo4j import AsyncDriver, AsyncGraphDatabase, Driver

from app.chat.hub import ChatHub
from app.core.config import Settings
from app.core.llm import ConfigError
from app.ingestion.build_topology import build_topology
from app.ingestion.common import run_read
from app.ingestion.compute_thresholds import compute_and_store_thresholds
from app.ingestion.embed_failures import embed_failures
from app.ingestion.init_schema import init_schema
from app.ingestion.load_events import load_events
from app.ingestion.reset_live_state import reset_live_state
from app.models.chat import ProactiveEvent
from app.streaming import messages as M
from app.streaming import writer
from app.streaming.consumer import StreamingService
from app.streaming.control import publish_command
from app.streaming.events import EventsHub
from app.streaming.simulator import SimulatorEngine, run
from tests.conftest import TEST_MQTT_PORT, TEST_NEO4J_PASSWORD, TEST_NEO4J_URI
from tests.fakes import HashingEmbedder

pytestmark = pytest.mark.integration

# fixture sensor stats: vibration ~ N(40, 5) -> warn 52.5, crit 57.5 (thresholds come from the CSV)


class RecordingEvents(EventsHub):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    async def broadcast(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


class RecordingChat(ChatHub):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[ProactiveEvent] = []

    async def broadcast(self, event: Any) -> int:
        self.sent.append(event)
        return 1


def no_llm() -> Any:
    raise ConfigError("no key in tests")


@pytest.fixture
def world(clean_db: Driver, pdm_dir: Path, test_settings: Settings, tmp_path: Path) -> Driver:
    init_schema(clean_db, test_settings)
    build_topology(clean_db, pdm_dir)
    load_events(clean_db, pdm_dir)
    compute_and_store_thresholds(clean_db, pdm_dir, tmp_path)
    embed_failures(clean_db, HashingEmbedder(), pdm_dir)
    return clean_db


@pytest_asyncio.fixture
async def adriver(world: Driver) -> AsyncIterator[AsyncDriver]:
    driver = AsyncGraphDatabase.driver(TEST_NEO4J_URI, auth=("neo4j", TEST_NEO4J_PASSWORD))
    yield driver
    await driver.close()


@pytest.fixture
def make_service(
    adriver: AsyncDriver,
) -> Callable[..., tuple[StreamingService, RecordingEvents, RecordingChat]]:
    def build(**kw: Any) -> tuple[StreamingService, RecordingEvents, RecordingChat]:
        events, chat = RecordingEvents(), RecordingChat()
        settings = Settings(_env_file=None, mqtt_port=TEST_MQTT_PORT)
        service = StreamingService(
            adriver,
            events,
            chat,
            settings,
            embedder=HashingEmbedder,
            llm=no_llm,
            **kw,
        )
        return service, events, chat

    return build


def telemetry(machine: str, ts: str, **values: float) -> bytes:
    base = {"volt": 170.0, "rotate": 446.0, "pressure": 100.0, "vibration": 40.0}
    base.update(values)
    return json.dumps(
        {"machine_id": machine, "ts": ts, "sim_ts": "2015-01-01T06:00:00", **base}
    ).encode()


async def feed(service: StreamingService, machine: str, vib: list[float], t0: int = 0) -> None:
    for i, v in enumerate(vib):
        ts = f"2026-01-01T00:00:{t0 + i:02d}+00:00"
        await service.handle_message(
            f"factory/P1/A1/{machine}/telemetry", telemetry(machine, ts, vibration=v)
        )


def alarms(driver: Driver) -> list[dict[str, Any]]:
    return run_read(
        driver,
        "MATCH (a:Alarm)-[:RAISED_ON]->(m:Machine) "
        "RETURN a.id AS id, m.id AS machine, a.status AS status, a.severity AS severity, "
        "a.source AS source, a.sensor_type AS sensor ORDER BY a.ts",
    )


async def test_readings_update_sensor_nodes_in_one_batch(
    world: Driver, make_service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, events, _ = make_service()
    calls = []
    real = writer.apply_batch

    async def counting(driver: AsyncDriver, batch: writer.BatchWrites) -> None:
        calls.append(batch)
        await real(driver, batch)

    monkeypatch.setattr(writer, "apply_batch", counting)
    for m in ("M001", "M002", "M003"):
        await feed(service, m, [41.0, 42.0])
    await service.flush()
    assert len(calls) == 1  # 3 machines x 2 readings = one micro-batch, one write
    row = run_read(
        world,
        "MATCH (s:Sensor {id: 'M001-vibration'}) "
        "RETURN s.latest_value AS v, s.avg_24h AS avg, toString(s.latest_ts) AS ts",
    )[0]
    assert (
        row["v"] == 42.0
        and row["avg"] == pytest.approx(41.5)
        and row["ts"].startswith("2026-01-01")
    )
    (update,) = events.of("sensor_update")
    assert len(update["readings"]) == 3 * 4  # latest value per sensor, once per batch


async def test_three_breaches_create_alarm_and_set_status(world: Driver, make_service: Any) -> None:
    service, events, _ = make_service()
    await feed(service, "M017", [60.0, 60.0])
    await service.flush()
    assert alarms(world) == []
    await feed(service, "M017", [60.0], t0=2)
    await service.flush()
    (alarm,) = alarms(world)
    assert alarm["machine"] == "M017" and alarm["status"] == "open"
    assert alarm["severity"] == "critical" and alarm["source"] == "simulator"
    assert run_read(world, "MATCH (m:Machine {id: 'M017'}) RETURN m.status AS s") == [
        {"s": "alarm"}
    ]
    assert run_read(world, "MATCH (:Alarm)-[:FROM_SENSOR]->(s:Sensor) RETURN s.id AS s") == [
        {"s": "M017-vibration"}
    ]
    (raised,) = events.of("alarm_raised")
    assert raised["alarm"]["machine_id"] == "M017" and raised["alarm"]["severity"] == "critical"
    assert events.of("machine_status") == [
        {"type": "machine_status", "machine_id": "M017", "status": "alarm"}
    ]


async def test_alarm_resolves_after_five_normal_readings(world: Driver, make_service: Any) -> None:
    service, events, _ = make_service()
    await feed(service, "M017", [60.0] * 3)
    await service.flush()
    await feed(service, "M017", [40.0] * 4, t0=3)
    await service.flush()
    assert alarms(world)[0]["status"] == "open"
    await feed(service, "M017", [40.0], t0=7)
    await service.flush()
    assert alarms(world)[0]["status"] == "resolved"
    assert run_read(world, "MATCH (m:Machine {id: 'M017'}) RETURN m.status AS s") == [
        {"s": "running"}
    ]
    assert len(events.of("alarm_resolved")) == 1


async def test_new_alarm_links_similar_failures_and_posts_a_proactive_chat_message(
    world: Driver, make_service: Any
) -> None:
    service, events, chat = make_service()
    await feed(service, "M017", [60.0] * 3)
    await service.flush()
    await service.wait_background()
    sims = run_read(
        world,
        "MATCH (a:Alarm)-[r:SIMILAR_TO]->(f:Failure) RETURN count(r) AS n, count(DISTINCT f) AS d",
    )
    assert sims == [{"n": 3, "d": 3}]
    assert run_read(world, "MATCH (a:Alarm) RETURN size(a.embedding) AS dims") == [{"dims": 1536}]
    (msg,) = chat.sent
    assert msg.message.startswith("⚠️ M017 vibration") and "diagnose M017" in msg.message
    assert "Similar to failure F-" in msg.message
    assert msg.actions[0].message == "Diagnose M017: vibration alarm"


async def test_escalation_updates_the_same_alarm_without_a_second_chat_message(
    world: Driver, make_service: Any
) -> None:
    service, events, chat = make_service()
    await feed(service, "M017", [54.0] * 3)  # warning
    await service.flush()
    await feed(service, "M017", [60.0] * 3, t0=3)  # critical
    await service.flush()
    await service.wait_background()
    (alarm,) = alarms(world)
    assert alarm["severity"] == "critical"
    assert [e["escalated"] for e in events.of("alarm_raised")] == [False, True]
    assert len(chat.sent) == 1


async def test_replayed_errors_are_stored_as_simulator_events(
    world: Driver, make_service: Any
) -> None:
    service, _, _ = make_service()
    payload = json.dumps(
        {
            "machine_id": "M004",
            "ts": "2026-01-01T00:00:00+00:00",
            "sim_ts": "2015-02-01T00:00:00",
            "error": "error2",
        }
    ).encode()
    await service.handle_message("factory/P1/A1/M004/errors", payload)
    await service.handle_message("factory/P1/A1/M004/errors", payload)  # duplicate id -> merged
    await service.flush()
    rows = run_read(
        world,
        "MATCH (e:ErrorEvent {source: 'simulator'})-[:OF_TYPE]->(t), (e)-[:OCCURRED_ON]->(m) "
        "RETURN m.id AS m, t.id AS t",
    )
    assert rows == [{"m": "M004", "t": "error2"}]


async def test_malformed_messages_are_dropped(world: Driver, make_service: Any) -> None:
    service, events, _ = make_service()
    await service.handle_message("factory/P1/A1/M001/telemetry", b"{not json")
    await service.handle_message("factory/P1/A1/M001/telemetry", b'{"machine_id": "M001"}')
    await service.handle_message("factory/control/sim", b'{"cmd": "start"}')
    await service.flush()
    assert events.events == []


async def test_open_alarms_survive_a_consumer_restart(world: Driver, make_service: Any) -> None:
    first, _, _ = make_service()
    await feed(first, "M017", [60.0] * 3)
    await first.flush()
    second, events, _ = make_service()  # fresh process: engine memory is empty
    await feed(second, "M017", [40.0] * 5)
    await second.flush()
    assert alarms(world)[0]["status"] == "resolved"
    assert len(events.of("alarm_resolved")) == 1


async def test_sim_status_messages_update_state_and_broadcast(make_service: Any) -> None:
    service, events, _ = make_service()
    await service.handle_message(
        M.STATUS_TOPIC, b'{"state": "paused", "speed": 2.0, "sim_time": "2015-03-01T00:00:00"}'
    )
    assert service.sim_status.state == "paused" and service.sim_status.speed == 2.0
    assert events.of("sim_status")[0]["state"] == "paused"


async def test_reset_live_state_keeps_history_and_structure(
    world: Driver, make_service: Any
) -> None:
    service, _, _ = make_service()
    await feed(service, "M017", [60.0] * 3)
    await service.flush()
    await service.wait_background()
    await service.handle_message(
        "factory/P1/A1/M004/errors",
        json.dumps(
            {
                "machine_id": "M004",
                "ts": "2026-01-01T00:00:00+00:00",
                "sim_ts": "x",
                "error": "error1",
            }
        ).encode(),
    )
    await service.flush()

    def snapshot() -> dict[str, Any]:
        return {
            "kaggle": run_read(
                world,
                "MATCH (n) WHERE n.source = 'kaggle' "
                "RETURN labels(n)[0] AS l, count(n) AS c ORDER BY l",
            ),
            "structure": run_read(
                world, "MATCH (n) WHERE n:Machine OR n:Sensor OR n:Component RETURN count(n) AS c"
            ),
            "embeddings": run_read(
                world, "MATCH (f:Failure) WHERE f.embedding IS NOT NULL RETURN count(f) AS c"
            ),
        }

    before = snapshot()
    summary = await asyncio.to_thread(reset_live_state, world)
    assert summary["alarms_deleted"] == 1 and summary["simulator_errors_deleted"] == 1
    assert snapshot() == before  # Kaggle history, structure and embeddings untouched
    assert run_read(world, "MATCH (a:Alarm) RETURN count(a) AS c") == [{"c": 0}]
    assert run_read(world, "MATCH (e:ErrorEvent {source: 'simulator'}) RETURN count(e) AS c") == [
        {"c": 0}
    ]
    assert run_read(
        world, "MATCH (m:Machine) WHERE m.status <> 'running' RETURN count(m) AS c"
    ) == [{"c": 0}]
    assert run_read(
        world, "MATCH (s:Sensor) WHERE s.latest_value IS NOT NULL RETURN count(s) AS c"
    ) == [{"c": 0}]
    # thresholds are structure, not live state
    assert run_read(world, "MATCH (s:Sensor) WHERE s.warn_high IS NULL RETURN count(s) AS c") == [
        {"c": 0}
    ]


# ---- end to end through a real MQTT broker --------------------------------------------------


def broker_up() -> bool:
    try:
        socket.create_connection(("localhost", TEST_MQTT_PORT), timeout=1).close()
        return True
    except OSError:
        return False


async def eventually(check: Callable[[], Any], wait_s: float = 20.0, step: float = 0.1) -> Any:
    deadline = asyncio.get_running_loop().time() + wait_s
    while True:
        value = check()
        if value:
            return value
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"condition not met within {wait_s}s")
        await asyncio.sleep(step)


@pytest.mark.skipif(not broker_up(), reason="test MQTT broker not running (compose --profile test)")
async def test_end_to_end_simulator_to_alarm_pause_and_reset(
    world: Driver, pdm_dir: Path, make_service: Any
) -> None:
    settings = Settings(_env_file=None, mqtt_port=TEST_MQTT_PORT)
    service, events, chat = make_service(flush_interval_s=0.2)
    engine = SimulatorEngine(pdm_dir, speed=20.0, loop=True, running=True)
    await service.start()
    sim = asyncio.create_task(run(engine, settings.mqtt_host, settings.mqtt_port))
    try:
        # start sim -> sensor values change in Neo4j
        await eventually(
            lambda: (
                run_read(
                    world, "MATCH (s:Sensor) WHERE s.latest_value IS NOT NULL RETURN count(s) AS c"
                )[0]["c"]
                >= 400
            )
        )
        assert service.sim_status.state == "running" and service.mqtt_connected
        first = run_read(world, "MATCH (s:Sensor {id: 'M017-volt'}) RETURN s.latest_value AS v")[0][
            "v"
        ]

        # inject anomaly on M017 -> alarm node, event on /ws/events, proactive chat message
        await publish_command(
            settings,
            M.SimCommand(
                cmd="inject", machine_id="M017", sensor="vibration", magnitude=5, duration_s=30
            ),
        )
        alarm = await eventually(
            lambda: [
                a for a in alarms(world) if a["machine"] == "M017" and a["sensor"] == "vibration"
            ]
        )
        assert alarm[0]["severity"] == "critical" and alarm[0]["source"] == "simulator"
        await eventually(lambda: events.of("alarm_raised"))
        await service.wait_background()
        await eventually(lambda: chat.sent)
        assert chat.sent[0].machine_id == "M017"
        assert (
            run_read(world, "MATCH (:Alarm)-[:SIMILAR_TO]->(f:Failure) RETURN count(f) AS c")[0][
                "c"
            ]
            == 3
        )

        # stop -> state paused, values freeze
        await publish_command(settings, M.SimCommand(cmd="stop"))
        await eventually(lambda: service.sim_status.state == "paused")
        await asyncio.sleep(0.6)  # let in-flight messages drain
        await service.flush()
        frozen = run_read(
            world, "MATCH (s:Sensor) RETURN s.id AS id, s.latest_value AS v ORDER BY id"
        )
        await asyncio.sleep(1.0)
        await service.flush()
        assert (
            run_read(world, "MATCH (s:Sensor) RETURN s.id AS id, s.latest_value AS v ORDER BY id")
            == frozen
        )
        assert frozen[0]["v"] is not None and first is not None

        # reset (simulator stopped) -> no alarms, all running, history intact
        kaggle = run_read(world, "MATCH (n) WHERE n.source = 'kaggle' RETURN count(n) AS c")
        await asyncio.to_thread(reset_live_state, world)
        await service.reset()
        assert alarms(world) == []
        assert (
            run_read(world, "MATCH (m:Machine) WHERE m.status <> 'running' RETURN count(m) AS c")[
                0
            ]["c"]
            == 0
        )
        assert run_read(world, "MATCH (n) WHERE n.source = 'kaggle' RETURN count(n) AS c") == kaggle
    finally:
        sim.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sim
        await service.stop()
