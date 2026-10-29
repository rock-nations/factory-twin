"""Graph queries behind the dashboard REST endpoints (async driver, read-only)."""

from typing import Any

from neo4j import READ_ACCESS, AsyncDriver

from app.graph.thresholds import classify_reading
from app.models.factory import (
    STATUSES,
    AlarmOut,
    GraphEdge,
    GraphNode,
    GraphView,
    LineSummary,
    MachineSummary,
    PlantSummary,
    SensorReading,
    StatusCounts,
)
from app.retrieval.serialization import jsonable

SENSOR_ORDER = ["volt", "rotate", "pressure", "vibration"]


async def _rows(driver: AsyncDriver, query: str, **params: Any) -> list[dict[str, Any]]:
    async with driver.session(default_access_mode=READ_ACCESS) as session:
        result = await session.run(query, **params)
        return [jsonable(r.data()) async for r in result]


def sensor_reading(raw: dict[str, Any]) -> SensorReading:
    value = raw.get("value")
    state = (
        "no_data"
        if value is None
        else classify_reading(
            value,
            raw.get("warn_high"),
            raw.get("crit_high"),
            raw.get("warn_low"),
            raw.get("crit_low"),
        )
    )
    return SensorReading(
        **{k: raw.get(k) for k in SensorReading.model_fields if k != "state"}, state=state
    )


async def list_plants(driver: AsyncDriver) -> list[PlantSummary]:
    rows = await _rows(
        driver,
        """
        MATCH (p:Plant)-[:HAS_LINE]->(l:ProductionLine)
        OPTIONAL MATCH (l)-[:HAS_MACHINE]->(m:Machine)
        RETURN p.id AS plant_id, p.name AS plant_name, p.location AS location,
               l.id AS line_id, l.name AS line_name, m.status AS status, count(m) AS n
        ORDER BY plant_id, line_id
        """,
    )
    plants: dict[str, PlantSummary] = {}
    lines: dict[tuple[str, str], LineSummary] = {}
    for r in rows:
        plant = plants.setdefault(
            r["plant_id"],
            PlantSummary(
                id=r["plant_id"], name=r["plant_name"], location=r["location"],
                machine_count=0, status_counts=StatusCounts(), lines=[],
            ),
        )  # fmt: skip
        key = (r["plant_id"], r["line_id"])
        if key not in lines:
            lines[key] = LineSummary(
                id=r["line_id"], name=r["line_name"], machine_count=0, status_counts=StatusCounts()
            )
            plant.lines.append(lines[key])
        if r["status"] in STATUSES and r["n"]:
            for target in (lines[key], plant):
                setattr(
                    target.status_counts,
                    r["status"],
                    getattr(target.status_counts, r["status"]) + r["n"],
                )
                target.machine_count += r["n"]
    return list(plants.values())


async def list_machines(
    driver: AsyncDriver, line_id: str | None = None, status: str | None = None
) -> list[MachineSummary]:
    rows = await _rows(
        driver,
        """
        MATCH (p:Plant)-[:HAS_LINE]->(l:ProductionLine)-[hm:HAS_MACHINE]->(m:Machine)
        WHERE ($line IS NULL OR l.id = $line) AND ($status IS NULL OR m.status = $status)
        MATCH (m)-[:IS_MODEL]->(mm:MachineModel)
        OPTIONAL MATCH (m)-[:HAS_SENSOR]->(s:Sensor)
        WITH p, l, hm, m, mm, collect(CASE WHEN s IS NULL THEN NULL ELSE {
            sensor: s.type, unit: s.unit, value: s.latest_value, as_of: toString(s.latest_ts),
            avg_24h: s.avg_24h, warn_high: s.warn_high, crit_high: s.crit_high,
            warn_low: s.warn_low, crit_low: s.crit_low } END) AS sensors
        RETURN m.id AS id, m.machine_id AS machine_id, m.age AS age, mm.id AS model,
               l.id AS line_id, p.id AS plant_id, hm.position AS position,
               m.status AS status, sensors
        ORDER BY plant_id, line_id, position
        """,
        line=line_id,
        status=status,
    )
    out = []
    for r in rows:
        sensors = sorted(
            (sensor_reading(s) for s in r.pop("sensors")),
            key=lambda s: SENSOR_ORDER.index(s.sensor) if s.sensor in SENSOR_ORDER else 99,
        )
        out.append(MachineSummary(**r, sensors=sensors))
    return out


async def list_alarms(
    driver: AsyncDriver,
    status: str | None = "open",
    machine_id: str | None = None,
    limit: int = 100,
) -> list[AlarmOut]:
    rows = await _rows(
        driver,
        """
        MATCH (a:Alarm)-[:RAISED_ON]->(m:Machine)
        WHERE ($status IS NULL OR a.status = $status) AND ($machine IS NULL OR m.id = $machine)
        OPTIONAL MATCH (a)-[r:SIMILAR_TO]->(f:Failure)
        WITH a, m, r, f ORDER BY r.score DESC
        WITH a, m,
             collect(CASE WHEN f IS NULL THEN NULL ELSE {failure_id: f.id, score: r.score} END)
             AS similar
        RETURN a.id AS id, m.id AS machine_id, a.sensor_type AS sensor_type, a.severity AS severity,
               a.value AS value, a.threshold AS threshold, a.status AS status, a.message AS message,
               toString(a.ts) AS ts, similar AS similar_failures
        ORDER BY a.ts DESC LIMIT $limit
        """,
        status=status,
        machine=machine_id,
        limit=limit,
    )
    return [AlarmOut(**r) for r in rows]


async def acknowledge_alarm(driver: AsyncDriver, alarm_id: str) -> str | None:
    """Returns the alarm's resulting status, or None when it does not exist."""
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (a:Alarm {id: $id})
            SET a.status = CASE a.status WHEN 'open' THEN 'acknowledged' ELSE a.status END
            RETURN a.status AS status
            """,
            id=alarm_id,
        )
        record = await result.single()
    return record["status"] if record else None


# ---- graph view ------------------------------------------------------------------------------

CAPTION_KEYS = ("name", "title", "message", "description", "type")
NOISY = {"ErrorEvent", "Chunk"}  # hundreds per machine / not useful to look at
HUBS = {"Plant", "MachineModel", "ErrorType", "Technician", "Document"}  # not expanded at depth 2
MAX_NODES = 150
PER_LABEL_AT_ROOT = 12
PREVIEW_PROPS = (
    "name",
    "status",
    "age",
    "severity",
    "sensor_type",
    "value",
    "ts",
    "doc_type",
    "type",
)


def _caption(node: dict[str, Any]) -> str:
    for key in CAPTION_KEYS:
        value = node.get(key)
        if value:
            return str(value)[:40]
    return str(node["id"])


def _to_node(raw: dict[str, Any]) -> GraphNode:
    label = raw["labels"][0]
    props = {k: raw["props"][k] for k in PREVIEW_PROPS if k in raw["props"]}
    return GraphNode(
        id=raw["props"]["id"], label=label, caption=_caption({**raw["props"]}), properties=props
    )


async def neighborhood(driver: AsyncDriver, node_id: str, depth: int = 1) -> GraphView | None:
    root_rows = await _rows(
        driver,
        "MATCH (n {id: $id}) RETURN labels(n) AS labels, properties(n) AS props LIMIT 1",
        id=node_id,
    )
    if not root_rows:
        return None
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    root = _to_node(root_rows[0])
    nodes[root.id] = root
    truncated = False

    async def expand(from_ids: list[str], per_label: int, skip_hubs: bool) -> list[str]:
        nonlocal truncated
        rows = await _rows(
            driver,
            """
            UNWIND $ids AS id
            MATCH (n {id: id})-[r]-(m)
            WHERE NOT any(l IN labels(m) WHERE l IN $noisy)
              AND NOT (m:Alarm AND m.status = 'resolved')
            WITH n, r, m, labels(m)[0] AS label
            ORDER BY label, coalesce(m.ts, datetime({year: 1970})) DESC
            WITH n, label, collect({r: r, m: m})[..$per_label] AS kept
            UNWIND kept AS k
            RETURN n.id AS source_node, type(k.r) AS type, startNode(k.r).id AS start,
                   endNode(k.r).id AS end, labels(k.m) AS labels, properties(k.m) AS props
            """,
            ids=from_ids,
            noisy=sorted(NOISY),
            per_label=per_label,
        )
        added: list[str] = []
        for r in rows:
            if r["labels"][0] in HUBS and skip_hubs:
                continue
            if r["props"]["id"] not in nodes:
                if len(nodes) >= MAX_NODES:
                    truncated = True
                    continue
                nodes[r["props"]["id"]] = _to_node(r)
                added.append(r["props"]["id"])
            edge_id = f"{r['start']}-{r['type']}-{r['end']}"
            edges.setdefault(
                edge_id, GraphEdge(id=edge_id, source=r["start"], target=r["end"], type=r["type"])
            )
        return added

    level1 = await expand([root.id], PER_LABEL_AT_ROOT, skip_hubs=False)
    if depth >= 2:
        expandable = [i for i in level1 if nodes[i].label not in HUBS]
        await expand(expandable, 4, skip_hubs=True)
    # edges only between nodes we kept
    kept_edges = [e for e in edges.values() if e.source in nodes and e.target in nodes]
    return GraphView(nodes=list(nodes.values()), edges=kept_edges, truncated=truncated)
