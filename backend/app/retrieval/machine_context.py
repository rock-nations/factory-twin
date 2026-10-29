"""get_machine_context: fixed, hand-written Cypher (deliberately NOT LLM-generated)."""

import re
from typing import Any

from neo4j import READ_ACCESS, Driver

from app.graph.thresholds import classify_reading
from app.retrieval.serialization import jsonable

_MACHINE_ID = re.compile(r"(\d{1,3})")

HEADER = """
MATCH (m:Machine {id: $id})-[:IS_MODEL]->(mm:MachineModel)
OPTIONAL MATCH (p:Plant)-[:HAS_LINE]->(l:ProductionLine)-[r:HAS_MACHINE]->(m)
RETURN m.id AS id, m.status AS status, m.age AS age, mm.id AS model,
       l.id AS line, r.position AS position_in_line, p.id AS plant_id, p.name AS plant
"""
SENSORS = """
MATCH (:Machine {id: $id})-[:HAS_SENSOR]->(s:Sensor)
RETURN s.type AS sensor, s.unit AS unit, s.latest_value AS value, s.latest_ts AS as_of,
       s.avg_24h AS avg_24h, s.warn_high AS warn_high, s.crit_high AS crit_high,
       s.warn_low AS warn_low, s.crit_low AS crit_low
ORDER BY sensor
"""
ALARMS = """
MATCH (a:Alarm {status: 'open'})-[:RAISED_ON]->(:Machine {id: $id})
OPTIONAL MATCH (a)-[s:SIMILAR_TO]->(f:Failure)
WITH a, s, f ORDER BY s.score DESC
RETURN a.id AS alarm, a.severity AS severity, a.sensor_type AS sensor, a.value AS value,
       a.threshold AS threshold, a.ts AS raised_at, a.message AS message,
       collect(CASE WHEN f IS NULL THEN NULL ELSE {failure: f.id, score: s.score} END) AS similar
ORDER BY a.ts DESC
"""
COMPONENTS = """
MATCH (:Machine {id: $id})-[:HAS_COMPONENT]->(c:Component)
RETURN c.type AS type, c.name AS component, c.last_replaced_at AS last_replaced
ORDER BY type
"""
MAINTENANCE = """
MATCH (e:MaintenanceEvent)-[:PERFORMED_ON]->(:Machine {id: $id})
MATCH (e)-[:REPLACED]->(c:Component)
OPTIONAL MATCH (e)-[:PERFORMED_BY]->(t:Technician)
RETURN e.ts AS ts, c.name AS component, e.reason AS reason, t.name AS technician
ORDER BY e.ts DESC LIMIT $limit
"""
DOWNSTREAM = """
MATCH p = (:Machine {id: $id})-[:FEEDS_INTO*1..2]->(d:Machine)
OPTIONAL MATCH (l:ProductionLine)-[:HAS_MACHINE]->(d)
RETURN d.id AS machine, d.status AS status, l.id AS line, min(length(p)) AS hops
ORDER BY hops, machine
"""
UPSTREAM = """
MATCH p = (u:Machine)-[:FEEDS_INTO*1..2]->(:Machine {id: $id})
OPTIONAL MATCH (l:ProductionLine)-[:HAS_MACHINE]->(u)
RETURN u.id AS machine, u.status AS status, l.id AS line, min(length(p)) AS hops
ORDER BY hops, machine
"""


def normalize_machine_id(raw: str) -> str | None:
    """'17', 'm17', 'Machine 017' -> 'M017'."""
    m = _MACHINE_ID.search(raw)
    return f"M{int(m.group(1)):03d}" if m else None


def get_machine_context(
    driver: Driver, machine_id: str, maintenance_limit: int = 5
) -> dict[str, Any]:
    mid = normalize_machine_id(machine_id)
    if mid is None:
        return {"error": f"'{machine_id}' is not a machine id (expected e.g. M017)"}

    def read(tx: Any, query: str) -> list[dict[str, Any]]:
        return [r.data() for r in tx.run(query, id=mid, limit=maintenance_limit)]

    with driver.session(default_access_mode=READ_ACCESS) as session:
        header = session.execute_read(read, HEADER)
        if not header:
            return {"error": f"Machine {mid} not found"}
        sensors = session.execute_read(read, SENSORS)
        alarms = session.execute_read(read, ALARMS)
        components = session.execute_read(read, COMPONENTS)
        maintenance = session.execute_read(read, MAINTENANCE)
        downstream = session.execute_read(read, DOWNSTREAM)
        upstream = session.execute_read(read, UPSTREAM)

    for s in sensors:
        s["state"] = (
            "no_data"
            if s["value"] is None
            else classify_reading(
                s["value"], s["warn_high"], s["crit_high"], s["warn_low"], s["crit_low"]
            )
        )
    context: dict[str, Any] = jsonable(
        {
            "machine": header[0],
            "sensors": sensors,
            "open_alarms": alarms,
            "components": components,
            "recent_maintenance": maintenance,
            "downstream": downstream,
            "upstream": upstream,
        }
    )
    return context
