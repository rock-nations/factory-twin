from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.streaming import messages as M
from app.streaming.simulator import Publication, SimulatorEngine

HOURS = 48


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def engine(pdm_dir: Path, clock: Clock) -> SimulatorEngine:
    return SimulatorEngine(
        pdm_dir, speed=0.5, monotonic=clock, wall_clock=lambda: "2026-01-01T00:00:00+00:00"
    )


def telemetry(pubs: list[Publication]) -> list[Publication]:
    return [(t, p) for t, p in pubs if t.endswith("/telemetry")]


def cmd(**kw: object) -> M.SimCommand:
    return M.SimCommand.model_validate(kw)


def test_first_tick_publishes_every_machine_on_its_topic(engine: SimulatorEngine) -> None:
    pubs = telemetry(engine.next_tick())
    assert len(pubs) == 100
    topic, payload = pubs[0]
    assert topic == "factory/P1/A1/M001/telemetry"
    assert M.parse_data_topic(topic) == ("telemetry", "P1", "A1", "M001")
    msg = M.TelemetryMsg.model_validate(payload)
    assert set(msg.readings()) == {"volt", "rotate", "pressure", "vibration"}
    assert msg.sim_ts and msg.sim_ts.startswith("2015-01-01T06")
    last_topic, _ = pubs[-1]
    assert last_topic == "factory/P2/B4/M100/telemetry"


def test_interval_follows_speed(engine: SimulatorEngine) -> None:
    assert engine.interval_s == 2.0  # 0.5 simulated hours per second -> a tick every 2 s
    engine.command(cmd(cmd="speed", speed=4))
    assert engine.interval_s == 0.25 and engine.status().speed == 4


def test_sim_time_advances_one_hour_per_tick(engine: SimulatorEngine) -> None:
    engine.next_tick()
    t1 = engine.status().sim_time
    engine.next_tick()
    t2 = engine.status().sim_time
    assert t1 and t2 and t2 > t1 and t2.startswith("2015-01-01T07")


def test_pause_and_resume(engine: SimulatorEngine) -> None:
    engine.next_tick()
    engine.command(cmd(cmd="stop"))
    assert engine.status().state == "paused" and engine.next_tick() == []
    sim_time = engine.status().sim_time
    engine.command(cmd(cmd="start"))
    assert engine.status().state == "running" and len(telemetry(engine.next_tick())) == 100
    assert engine.status().sim_time != sim_time


def test_errors_are_replayed_once_each(pdm_dir: Path, engine: SimulatorEngine) -> None:
    expected = pd.read_csv(pdm_dir / "PdM_errors.csv").drop_duplicates()
    seen: list[tuple[str, str]] = []
    for _ in range(HOURS):
        for topic, payload in engine.next_tick():
            if topic.endswith("/errors"):
                assert M.ErrorMsg.model_validate(payload).error.startswith("error")
                seen.append((payload["machine_id"], payload["error"]))
    want = [(f"M{r.machineID:03d}", str(r.errorID)) for r in expected.itertuples()]
    assert sorted(seen) == sorted(want)


def test_finishes_at_end_of_data(engine: SimulatorEngine) -> None:
    for _ in range(HOURS):
        assert engine.next_tick()
    assert engine.next_tick() == [] and engine.state == "finished"
    assert engine.status().state == "finished"


def test_start_after_finished_replays_with_a_forward_clock(engine: SimulatorEngine) -> None:
    for _ in range(HOURS):
        engine.next_tick()
    last = engine.status().sim_time
    engine.next_tick()
    engine.command(cmd(cmd="start"))
    engine.next_tick()
    now = engine.status().sim_time
    assert last and now and now > last


def test_loop_restarts_and_the_simulated_clock_keeps_moving(pdm_dir: Path, clock: Clock) -> None:
    e = SimulatorEngine(pdm_dir, loop=True, monotonic=clock)
    times: list[str] = []
    for _ in range(HOURS + 3):
        assert e.next_tick()
        times.append(e.status().sim_time or "")
    assert e.state == "running"
    assert times == sorted(times) and len(set(times)) == len(times)


def test_anomaly_injection_shifts_only_the_target_sensor_and_expires(
    engine: SimulatorEngine, clock: Clock
) -> None:
    base = {p["machine_id"]: p for _, p in telemetry(engine.next_tick())}
    engine.command(
        cmd(cmd="inject", machine_id="M017", sensor="vibration", magnitude=4, duration_s=10)
    )
    during = {p["machine_id"]: p for _, p in telemetry(engine.next_tick())}
    m17 = during["M017"]
    assert m17["vibration"] > engine._mean["vibration"] + 3.9 * engine._sigma["vibration"]
    other = during["M018"]
    assert other["vibration"] < engine._mean["vibration"] + 3 * engine._sigma["vibration"]
    assert during["M017"]["volt"] != base["M017"]["volt"]  # different hour, but not shifted by +σ
    clock.t += 11  # past duration_s
    after = {p["machine_id"]: p for _, p in telemetry(engine.next_tick())}
    assert after["M017"]["vibration"] < engine._mean["vibration"] + 3 * engine._sigma["vibration"]


def test_negative_injection_pushes_below_normal(engine: SimulatorEngine) -> None:
    engine.command(cmd(cmd="inject", machine_id="M005", sensor="volt", magnitude=-4))
    payload = next(p for _, p in telemetry(engine.next_tick()) if p["machine_id"] == "M005")
    assert payload["volt"] <= engine._mean["volt"] - 4 * engine._sigma["volt"] + 0.01


@pytest.mark.parametrize(
    "kw", [dict(machine_id="M999", sensor="volt"), dict(machine_id="M001", sensor="humidity")]
)
def test_invalid_injection_is_rejected(engine: SimulatorEngine, kw: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        engine.command(cmd(cmd="inject", **kw))


def test_injection_defaults(engine: SimulatorEngine) -> None:
    engine.command(cmd(cmd="inject", machine_id="M001", sensor="rotate"))
    ((_, inj),) = engine._injections.items()
    assert inj.magnitude == 4.0 and inj.until == 1000.0 + 60.0


def test_can_start_paused(pdm_dir: Path) -> None:
    e = SimulatorEngine(pdm_dir, running=False)
    assert e.state == "paused" and e.next_tick() == []
    assert e.status().sim_time is None


def test_command_and_topic_parsing() -> None:
    assert M.parse_data_topic("factory/control/sim/status") is None
    assert M.parse_data_topic("factory/P1/A2/M017/errors") == ("errors", "P1", "A2", "M017")
    with pytest.raises(ValueError):
        M.SimCommand.model_validate({"cmd": "explode"})
