"""MQTT consumer + alarm engine, running inside the FastAPI process (spec §7.2).

Messages are collected and flushed in 1 s micro-batches: one Neo4j transaction and at most one
`sensor_update` broadcast per batch.
"""

import asyncio
import contextlib
import hashlib
import logging
import os
from collections.abc import Callable
from typing import Any

import aiomqtt
from neo4j import AsyncDriver
from pydantic import ValidationError

from app.chat.hub import ChatHub
from app.chat.proactive import build_proactive_event
from app.core.config import Settings, get_settings
from app.core.embeddings import Embedder
from app.core.llm import ConfigError, TextCompleter
from app.graph import constants as C
from app.ingestion.telemetry_stats import SensorWindow, describe_window
from app.streaming import messages as M
from app.streaming import writer
from app.streaming.alarm_engine import AlarmEngine, AlarmRaised, AlarmResolved
from app.streaming.buffer import TelemetryBuffer
from app.streaming.events import EventsHub

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 1.0
MAX_BACKOFF_S = 30.0


class StreamingService:
    def __init__(
        self,
        driver: AsyncDriver,
        events: EventsHub,
        chat_hub: ChatHub,
        settings: Settings | None = None,
        embedder: Callable[[], Embedder] | None = None,
        llm: Callable[[], TextCompleter] | None = None,
        flush_interval_s: float = FLUSH_INTERVAL_S,
    ) -> None:
        self.driver = driver
        self.events = events
        self.chat_hub = chat_hub
        self.settings = settings or get_settings()
        self._embedder_factory = embedder
        self._llm_factory = llm
        self.flush_interval_s = flush_interval_s

        self.sim_status = M.SimStatus(state="offline", speed=self.settings.sim_speed)
        self.mqtt_connected = False
        self.buffer = TelemetryBuffer()
        self.engine = AlarmEngine({})
        self._machine_status: dict[str, str] = {}
        self._pending_telemetry: list[M.TelemetryMsg] = []
        self._pending_errors: list[M.ErrorMsg] = []
        self._initialised = False
        self._tasks: set[asyncio.Task[Any]] = set()
        self._background: set[asyncio.Task[Any]] = set()
        self._flush_lock = asyncio.Lock()
        self._embedder: Embedder | None = None
        self._llm: TextCompleter | None = None

    # ---- lifecycle --------------------------------------------------------------------------

    async def start(self) -> None:
        for coro in (self._mqtt_loop(), self._flush_loop()):
            task = asyncio.create_task(coro)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        for task in [*self._tasks, *self._background]:
            task.cancel()
        for task in [*self._tasks, *self._background]:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _ensure_initialised(self) -> bool:
        """Load thresholds, still-open alarms and machine statuses from the graph."""
        if self._initialised:
            return True
        try:
            thresholds = await writer.load_thresholds(self.driver)
            if not thresholds:
                logger.warning("no sensor thresholds in the graph yet; run the ingestion first")
                return False
            self.engine = AlarmEngine(thresholds)
            for machine, sensor, alarm_id, severity in await writer.load_open_alarms(self.driver):
                self.engine.seed_open_alarm(machine, sensor, alarm_id, severity)
            self._machine_status = await writer.load_machine_statuses(self.driver)
        except Exception as exc:
            logger.warning("streaming not ready (%s: %s); will retry", type(exc).__name__, exc)
            return False
        self._initialised = True
        logger.info(
            "streaming ready: %d sensor types, %d open alarms",
            len(thresholds),
            self.engine.open_alarm_count(),
        )
        return True

    async def reset(self) -> None:
        """Forget live state after `reset_live_state` wiped the graph side."""
        self._pending_telemetry.clear()
        self._pending_errors.clear()
        self.buffer.clear()
        self.engine.reset()
        self._machine_status = {k: "running" for k in self._machine_status}
        await self.events.broadcast({"type": "live_state_reset"})

    # ---- MQTT -------------------------------------------------------------------------------

    async def _mqtt_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with aiomqtt.Client(
                    self.settings.mqtt_host,
                    self.settings.mqtt_port,
                    identifier=f"factory-consumer-{os.getpid()}",
                ) as client:
                    await client.subscribe(M.SubscribeAll)
                    self.mqtt_connected = True
                    backoff = 1.0
                    logger.info("MQTT consumer subscribed to %s", M.SubscribeAll)
                    async for msg in client.messages:
                        await self.handle_message(str(msg.topic), bytes(msg.payload))
            except aiomqtt.MqttError as exc:
                logger.warning("MQTT connection lost (%s); retrying in %.0fs", exc, backoff)
            finally:
                self.mqtt_connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)

    async def handle_message(self, topic: str, payload: bytes) -> None:
        if topic == M.STATUS_TOPIC:
            try:
                self.sim_status = M.SimStatus.model_validate_json(payload)
            except ValidationError:
                return
            await self.events.broadcast({"type": "sim_status", **self.sim_status.model_dump()})
            return
        parsed = M.parse_data_topic(topic)
        if parsed is None:
            return  # control messages etc.
        try:
            if parsed[0] == "telemetry":
                self._pending_telemetry.append(M.TelemetryMsg.model_validate_json(payload))
            else:
                self._pending_errors.append(M.ErrorMsg.model_validate_json(payload))
        except ValidationError as exc:
            logger.warning(
                "dropping malformed %s message on %s: %s", parsed[0], topic, exc.errors()[:1]
            )

    # ---- micro-batches ----------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval_s)
            try:
                await self.flush()
            except Exception:
                logger.exception("flush failed")

    async def flush(self) -> None:
        async with self._flush_lock:
            if not (self._pending_telemetry or self._pending_errors):
                return
            if not await self._ensure_initialised():
                self._pending_telemetry.clear()  # nothing to attach them to; don't grow unbounded
                self._pending_errors.clear()
                return
            telemetry, self._pending_telemetry = self._pending_telemetry, []
            errors, self._pending_errors = self._pending_errors, []

            batch = writer.BatchWrites()
            latest: dict[tuple[str, str], tuple[str, float]] = {}
            raised: list[AlarmRaised] = []
            touched: set[str] = set()
            for msg in telemetry:
                for sensor, value in msg.readings().items():
                    self.buffer.add(msg.machine_id, sensor, msg.ts, value)
                    latest[(msg.machine_id, sensor)] = (msg.ts, value)
                    touched.add(msg.machine_id)
                    for action in self.engine.process(msg.machine_id, sensor, value, msg.ts):
                        if isinstance(action, AlarmRaised):
                            batch.raised.append(action)
                            raised.append(action)
                        elif isinstance(action, AlarmResolved):
                            batch.resolved.append(action)
            batch.sensors = [
                {
                    "id": f"{m}-{s}",
                    "value": v,
                    "ts": ts,
                    "avg": self.buffer.rolling_avg(m, s),
                }
                for (m, s), (ts, v) in latest.items()
            ]
            status_changes = []
            for machine in sorted(touched):
                status = self.engine.machine_status(machine)
                if self._machine_status.get(machine) != status:
                    status_changes.append({"id": machine, "status": status})
            batch.machine_status = status_changes
            batch.errors = [
                {
                    "id": "ERRSIM-"
                    + hashlib.sha1(f"{e.machine_id}|{e.sim_ts}|{e.error}".encode()).hexdigest()[
                        :16
                    ],
                    "ts": e.ts,
                    "machine": e.machine_id,
                    "error": e.error,
                }
                for e in errors
                if e.error in C.ERROR_TYPES
            ]

            await writer.apply_batch(self.driver, batch)
            for change in status_changes:
                self._machine_status[change["id"]] = change["status"]

        # ---- broadcasts (outside the lock; DB write already committed) -----------------------
        if latest:
            await self.events.broadcast(
                {
                    "type": "sensor_update",
                    "sim_time": self.sim_status.sim_time,
                    "readings": [
                        {"machine_id": m, "sensor": s, "value": v, "ts": ts}
                        for (m, s), (ts, v) in latest.items()
                    ],
                }
            )
        for change in status_changes:
            await self.events.broadcast(
                {"type": "machine_status", "machine_id": change["id"], "status": change["status"]}
            )
        for alarm in raised:
            await self.events.broadcast(
                {
                    "type": "alarm_raised",
                    "escalated": alarm.escalated,
                    "alarm": {
                        "id": alarm.alarm_id,
                        "machine_id": alarm.machine_id,
                        "sensor_type": alarm.sensor,
                        "severity": alarm.severity,
                        "value": alarm.value,
                        "threshold": alarm.threshold,
                        "sigma": alarm.sigma,
                        "message": alarm.message,
                        "ts": alarm.ts,
                        "status": "open",
                    },
                }
            )
            if not alarm.escalated:
                self._spawn(self._enrich_and_notify(alarm))
        for done in batch.resolved:
            await self.events.broadcast(
                {
                    "type": "alarm_resolved",
                    "alarm_id": done.alarm_id,
                    "machine_id": done.machine_id,
                    "sensor_type": done.sensor,
                }
            )

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # ---- alarm enrichment + proactive chat ---------------------------------------------------

    def _get_embedder(self) -> Embedder:
        if self._embedder is None:
            if self._embedder_factory is None:
                from app.core.embeddings import OpenAIEmbedder

                self._embedder = OpenAIEmbedder(self.settings)
            else:
                self._embedder = self._embedder_factory()
        return self._embedder

    def _get_llm(self) -> TextCompleter | None:
        if self._llm is None:
            try:
                if self._llm_factory is None:
                    from app.core.llm import LLM

                    self._llm = LLM(self.settings)
                else:
                    self._llm = self._llm_factory()
            except ConfigError:
                return None
        return self._llm

    def alarm_description(self, alarm: AlarmRaised, facts: dict[str, Any]) -> str:
        """Phrased like embed_failures' descriptions so the vectors are comparable."""
        windows = []
        for sensor, th in self.engine.thresholds.items():
            vals = self.buffer.values(alarm.machine_id, sensor, 24)
            if vals:
                zs = [th.zscore(v) for v in vals]
                peak = max(zs, key=abs)
                windows.append(SensorWindow(sensor, sum(zs) / len(zs), peak, sum(vals) / len(vals)))
        return (
            f"{alarm.sensor.capitalize()} deviation on {facts['model']} machine {alarm.machine_id} "
            f"(age {facts['age']} years). Sensors in the prior 24h: {describe_window(windows)}."
        )

    async def _enrich_and_notify(self, alarm: AlarmRaised) -> None:
        similar: list[dict[str, Any]] = []
        try:
            facts = await writer.machine_facts(self.driver, alarm.machine_id)
            if facts:
                text = self.alarm_description(alarm, facts)
                vector = (await asyncio.to_thread(self._get_embedder().embed, [text]))[0]
                similar = await writer.link_similar_failures(self.driver, alarm.alarm_id, vector)
        except ConfigError as exc:
            logger.info("alarm %s not enriched: %s", alarm.alarm_id, exc)
        except Exception:
            logger.exception("alarm enrichment failed for %s", alarm.alarm_id)
        try:
            event, usage = await build_proactive_event(
                alarm, similar[0] if similar else None, self._get_llm(), self.settings
            )
            delivered = await self.chat_hub.broadcast(event)
            logger.info(
                "proactive message for %s delivered to %d chat(s)", alarm.alarm_id, delivered
            )
        except Exception:
            logger.exception("proactive message failed for %s", alarm.alarm_id)

    async def wait_background(self) -> None:
        """For tests: wait until alarm enrichment/notifications have finished."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)
