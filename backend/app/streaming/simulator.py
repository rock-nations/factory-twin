"""Sensor simulator: replays the PdM telemetry/errors CSVs over MQTT (spec §7.1).

    python -m app.streaming.simulator              # start replaying at SIM_SPEED
    python -m app.streaming.simulator --paused --loop

Controlled through `factory/control/sim` (start | stop | speed | inject); its state is published,
retained, on `factory/control/sim/status` (with an `offline` last-will).
"""

import argparse
import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiomqtt
import numpy as np
import pandas as pd
from pydantic import ValidationError

from app.core.config import get_settings
from app.graph import constants as C
from app.ingestion.build_topology import plan_topology
from app.ingestion.common import DEFAULT_RAW_DIR, read_pdm_csv
from app.streaming import messages as M

logger = logging.getLogger(__name__)

DEFAULT_INJECT_DURATION_S = 60.0
DEFAULT_INJECT_MAGNITUDE = 4.0
SENSORS = list(C.SENSOR_UNITS)

Publication = tuple[str, dict[str, Any]]


@dataclass
class _Injection:
    magnitude: float
    until: float  # engine clock seconds


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SimulatorEngine:
    """All simulator behaviour except sockets and sleeping."""

    def __init__(
        self,
        raw_dir: Path = DEFAULT_RAW_DIR,
        speed: float = 0.5,
        loop: bool = False,
        running: bool = True,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self.speed = speed
        self.loop = loop
        self.state: M.SimState = "running" if running else "paused"

        machines = read_pdm_csv(raw_dir, "PdM_machines.csv")
        plan = plan_topology([int(m) for m in machines["machineID"]])
        plants = dict(C.LINES)
        self._topics: dict[str, tuple[str, str]] = {}  # machine -> (telemetry topic, errors topic)
        for node, (line, _pos) in plan.placement.items():
            plant = plants[line]
            self._topics[node] = (
                M.telemetry_topic(plant, line, node),
                M.errors_topic(plant, line, node),
            )

        tele = read_pdm_csv(raw_dir, "PdM_telemetry.csv")
        tele["datetime"] = pd.to_datetime(tele["datetime"])
        tele = tele.sort_values(["datetime", "machineID"]).reset_index(drop=True)
        self._sigma = {s: float(tele[s].std()) for s in SENSORS}
        self._mean = {s: float(tele[s].mean()) for s in SENSORS}
        times = tele["datetime"].to_numpy()
        starts = np.unique(times, return_index=True)[1]
        self._bounds = list(zip(starts, [*starts[1:], len(tele)], strict=True))
        self._tick_times = [pd.Timestamp(times[a]) for a, _ in self._bounds]
        self._machine_ids = [C.machine_node_id(int(m)) for m in tele["machineID"]]
        self._values = tele[SENSORS].to_numpy(dtype=float)
        self._span = (self._tick_times[-1] - self._tick_times[0]) + timedelta(hours=1)

        errors = read_pdm_csv(raw_dir, "PdM_errors.csv")
        errors["datetime"] = pd.to_datetime(errors["datetime"])
        errors = errors.sort_values(["datetime", "machineID"]).reset_index(drop=True)
        self._errors: list[tuple[pd.Timestamp, str, str]] = [
            (
                pd.Timestamp(str(row.datetime)),
                C.machine_node_id(int(str(row.machineID))),
                str(row.errorID),
            )
            for row in errors.itertuples()
        ]
        self._tick = 0
        self._error_ptr = 0
        self._loops = 0
        self._last_sim_time: pd.Timestamp | None = None
        self._injections: dict[tuple[str, str], _Injection] = {}

    # ---- state ------------------------------------------------------------------------------

    @property
    def interval_s(self) -> float:
        """Real seconds per tick (one simulated hour)."""
        return 1.0 / self.speed

    def status(self) -> M.SimStatus:
        sim_time = self._last_sim_time.isoformat() if self._last_sim_time is not None else None
        return M.SimStatus(state=self.state, speed=self.speed, sim_time=sim_time, loop=self.loop)

    def command(self, cmd: M.SimCommand) -> None:
        if cmd.cmd == "start":
            if self.state in ("paused", "finished"):
                if self.state == "finished":
                    self._rewind()
                self.state = "running"
        elif cmd.cmd == "stop":
            if self.state == "running":
                self.state = "paused"
        elif cmd.cmd == "speed":
            if cmd.speed is not None:
                self.speed = cmd.speed
        elif cmd.cmd == "inject":
            self._inject(cmd)

    def _rewind(self) -> None:
        self._tick = self._error_ptr = 0
        self._loops += 1  # simulated clock keeps moving forward

    def _inject(self, cmd: M.SimCommand) -> None:
        if cmd.machine_id not in self._topics:
            raise ValueError(f"unknown machine {cmd.machine_id!r}")
        if cmd.sensor not in self._sigma:
            raise ValueError(f"unknown sensor {cmd.sensor!r}")
        duration = cmd.duration_s or DEFAULT_INJECT_DURATION_S
        magnitude = DEFAULT_INJECT_MAGNITUDE if cmd.magnitude is None else cmd.magnitude
        self._injections[(cmd.machine_id, cmd.sensor)] = _Injection(
            magnitude, self._monotonic() + duration
        )
        logger.info(
            "injecting %+.1fσ on %s/%s for %.0fs", magnitude, cmd.machine_id, cmd.sensor, duration
        )

    # ---- ticks ------------------------------------------------------------------------------

    def next_tick(self) -> list[Publication]:
        """Messages for the next simulated hour, or [] when paused/finished."""
        if self.state != "running":
            return []
        if self._tick >= len(self._bounds):
            if not self.loop:
                self.state = "finished"
                return []
            self._rewind()

        start, end = self._bounds[self._tick]
        sim_ts = self._tick_times[self._tick]
        shifted = sim_ts + self._span * self._loops
        self._last_sim_time = shifted
        ts = self._wall_clock()
        now = self._monotonic()
        self._injections = {k: v for k, v in self._injections.items() if v.until > now}

        out: list[Publication] = []
        for i in range(start, end):
            machine = self._machine_ids[i]
            readings = dict(zip(SENSORS, self._values[i], strict=True))
            for (m, sensor), inj in self._injections.items():
                if m == machine:
                    base = readings[sensor]
                    anchor = (
                        max(base, self._mean[sensor])
                        if inj.magnitude > 0
                        else min(base, self._mean[sensor])
                    )
                    readings[sensor] = anchor + inj.magnitude * self._sigma[sensor]
            payload = {
                "machine_id": machine,
                "ts": ts,
                "sim_ts": shifted.isoformat(),
                **{s: round(float(v), 2) for s, v in readings.items()},
            }
            out.append((self._topics[machine][0], payload))

        while self._error_ptr < len(self._errors) and self._errors[self._error_ptr][0] <= sim_ts:
            err_ts, machine, code = self._errors[self._error_ptr]
            self._error_ptr += 1
            if err_ts == sim_ts:  # skip errors that predate the first replayed hour
                out.append(
                    (
                        self._topics[machine][1],
                        {
                            "machine_id": machine,
                            "ts": ts,
                            "sim_ts": shifted.isoformat(),
                            "error": code,
                        },
                    )
                )
        self._tick += 1
        return out


# ---- MQTT runner ---------------------------------------------------------------------------


def _status_payload(status: M.SimStatus) -> str:
    return status.model_dump_json()


async def run(engine: SimulatorEngine, host: str, port: int) -> None:
    offline = _status_payload(M.SimStatus(state="offline", speed=engine.speed, loop=engine.loop))
    will = aiomqtt.Will(M.STATUS_TOPIC, offline, qos=1, retain=True)
    async with aiomqtt.Client(host, port, will=will, identifier="factory-simulator") as client:
        logger.info("simulator connected to %s:%d (state=%s)", host, port, engine.state)
        await client.subscribe(M.CONTROL_TOPIC)

        async def publish_status() -> None:
            await client.publish(
                M.STATUS_TOPIC, _status_payload(engine.status()), qos=1, retain=True
            )

        async def control() -> None:
            async for msg in client.messages:
                try:
                    engine.command(M.SimCommand.model_validate_json(bytes(msg.payload)))
                except (ValidationError, ValueError) as exc:
                    logger.warning("ignoring bad control message: %s", exc)
                    continue
                await publish_status()

        async def ticker() -> None:
            deadline = time.monotonic()
            last_state = None
            while True:
                for topic, payload in engine.next_tick():
                    await client.publish(topic, json.dumps(payload))
                if engine.state != last_state or engine.state == "running":
                    last_state = engine.state
                    await publish_status()
                deadline += engine.interval_s
                await asyncio.sleep(max(0.0, deadline - time.monotonic()))
                if engine.state != "running":
                    deadline = time.monotonic()  # no catch-up burst after a pause

        try:
            await publish_status()
            async with asyncio.TaskGroup() as tg:
                tg.create_task(control())
                tg.create_task(ticker())
        finally:
            # graceful disconnect does not trigger the last-will, so say goodbye explicitly
            offline_msg = _status_payload(
                M.SimStatus(state="offline", speed=engine.speed, loop=engine.loop)
            )
            await asyncio.shield(client.publish(M.STATUS_TOPIC, offline_msg, qos=1, retain=True))


def main() -> None:
    settings = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument(
        "--speed", type=float, default=settings.sim_speed, help="simulated hours per second"
    )
    p.add_argument(
        "--loop", action="store_true", help="restart from the first timestamp at the end"
    )
    p.add_argument("--paused", action="store_true", help="wait for a `start` command")
    p.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = SimulatorEngine(args.raw_dir, args.speed, args.loop, running=not args.paused)
    try:
        asyncio.run(run(engine, settings.mqtt_host, settings.mqtt_port))
    except KeyboardInterrupt:
        logger.info("simulator stopped")


if __name__ == "__main__":
    main()
