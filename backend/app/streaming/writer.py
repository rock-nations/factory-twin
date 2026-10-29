"""Neo4j writes for the live pipeline. One transaction per micro-batch (spec §7.1a)."""

import logging
from dataclasses import dataclass, field
from typing import Any

from neo4j import READ_ACCESS, AsyncDriver, AsyncManagedTransaction

from app.graph.thresholds import SensorThresholds
from app.streaming.alarm_engine import AlarmRaised, AlarmResolved, Severity

logger = logging.getLogger(__name__)


@dataclass
class BatchWrites:
    sensors: list[dict[str, Any]] = field(default_factory=list)
    machine_status: list[dict[str, Any]] = field(default_factory=list)
    raised: list[AlarmRaised] = field(default_factory=list)
    resolved: list[AlarmResolved] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def empty(self) -> bool:
        return not (
            self.sensors or self.machine_status or self.raised or self.resolved or self.errors
        )


SENSORS_Q = """
UNWIND $rows AS row
MATCH (s:Sensor {id: row.id})
SET s.latest_value = row.value, s.latest_ts = datetime(row.ts), s.avg_24h = row.avg
"""
STATUS_Q = """
UNWIND $rows AS row
MATCH (m:Machine {id: row.id})
SET m.status = row.status
"""
RAISED_Q = """
UNWIND $rows AS row
MERGE (a:Alarm {id: row.id})
ON CREATE SET a.ts = datetime(row.ts), a.status = 'open', a.source = 'simulator'
SET a.severity = row.severity, a.sensor_type = row.sensor, a.value = row.value,
    a.threshold = row.threshold, a.message = row.message
WITH a, row
MATCH (m:Machine {id: row.machine}), (s:Sensor {id: row.machine + '-' + row.sensor})
MERGE (a)-[:RAISED_ON]->(m)
MERGE (a)-[:FROM_SENSOR]->(s)
"""
RESOLVED_Q = """
UNWIND $ids AS id
MATCH (a:Alarm {id: id})
SET a.status = 'resolved'
"""
ERRORS_Q = """
UNWIND $rows AS row
MERGE (e:ErrorEvent {id: row.id})
SET e.ts = datetime(row.ts), e.source = 'simulator'
WITH e, row
MATCH (m:Machine {id: row.machine})
MERGE (e)-[:OCCURRED_ON]->(m)
WITH e, row
MATCH (t:ErrorType {id: row.error})
MERGE (e)-[:OF_TYPE]->(t)
"""


async def apply_batch(driver: AsyncDriver, batch: BatchWrites) -> None:
    if batch.empty():
        return

    async def work(tx: AsyncManagedTransaction) -> None:
        if batch.sensors:
            await tx.run(SENSORS_Q, rows=batch.sensors)
        if batch.raised:
            await tx.run(
                RAISED_Q,
                rows=[
                    {
                        "id": a.alarm_id,
                        "machine": a.machine_id,
                        "sensor": a.sensor,
                        "severity": a.severity,
                        "value": a.value,
                        "threshold": a.threshold,
                        "message": a.message,
                        "ts": a.ts,
                    }
                    for a in batch.raised
                ],
            )
        if batch.resolved:
            await tx.run(RESOLVED_Q, ids=[a.alarm_id for a in batch.resolved])
        if batch.machine_status:
            await tx.run(STATUS_Q, rows=batch.machine_status)
        if batch.errors:
            await tx.run(ERRORS_Q, rows=batch.errors)

    async with driver.session() as session:
        await session.execute_write(work)


async def _read(driver: AsyncDriver, query: str) -> list[dict[str, Any]]:
    async with driver.session(default_access_mode=READ_ACCESS) as session:
        result = await session.run(query)
        return [r.data() async for r in result]


async def load_thresholds(driver: AsyncDriver) -> dict[str, SensorThresholds]:
    rows = await _read(
        driver,
        """
        MATCH (s:Sensor) WHERE s.warn_high IS NOT NULL
        RETURN s.type AS sensor, head(collect(s.unit)) AS unit,
               head(collect(s.warn_high)) AS warn_high, head(collect(s.crit_high)) AS crit_high,
               head(collect(s.warn_low)) AS warn_low, head(collect(s.crit_low)) AS crit_low
        """,
    )
    return {r["sensor"]: SensorThresholds(**r) for r in rows}


async def load_open_alarms(driver: AsyncDriver) -> list[tuple[str, str, str, Severity]]:
    rows = await _read(
        driver,
        """
        MATCH (a:Alarm {source: 'simulator'})-[:RAISED_ON]->(m:Machine)
        WHERE a.status IN ['open', 'acknowledged']
        RETURN a.id AS id, m.id AS machine, a.sensor_type AS sensor, a.severity AS severity
        """,
    )
    return [(r["machine"], r["sensor"], r["id"], r["severity"]) for r in rows]


async def load_machine_statuses(driver: AsyncDriver) -> dict[str, str]:
    rows = await _read(driver, "MATCH (m:Machine) RETURN m.id AS id, m.status AS status")
    return {r["id"]: r["status"] for r in rows}


async def machine_facts(driver: AsyncDriver, machine_id: str) -> dict[str, Any] | None:
    async with driver.session(default_access_mode=READ_ACCESS) as session:
        result = await session.run(
            "MATCH (m:Machine {id: $id})-[:IS_MODEL]->(mm) RETURN mm.id AS model, m.age AS age",
            id=machine_id,
        )
        record = await result.single()
        return record.data() if record else None


async def link_similar_failures(
    driver: AsyncDriver, alarm_id: str, embedding: list[float], top_k: int = 3
) -> list[dict[str, Any]]:
    """Store the alarm embedding and write SIMILAR_TO to the top-k most similar past failures."""

    async def work(tx: AsyncManagedTransaction) -> list[dict[str, Any]]:
        result = await tx.run(
            """
            MATCH (a:Alarm {id: $id})
            SET a.embedding = $vec
            WITH a
            CALL db.index.vector.queryNodes('failure_embedding', $k, $vec) YIELD node, score
            MATCH (node)-[:FAILED]->(c:Component)
            MERGE (a)-[r:SIMILAR_TO]->(node)
            SET r.score = score
            RETURN node.id AS failure, c.name AS component, c.type AS component_type, score
            ORDER BY score DESC
            """,
            id=alarm_id,
            vec=embedding,
            k=top_k,
        )
        return [r.data() async for r in result]

    async with driver.session() as session:
        return await session.execute_write(work)
