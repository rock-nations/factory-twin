"""Plants, lines, machines, models, components, sensors, technicians and FEEDS_INTO (spec §4.2)."""

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from neo4j import Driver

from app.core.neo4j import create_sync_driver
from app.graph import constants as C
from app.ingestion.common import (
    DEFAULT_RAW_DIR,
    IngestionError,
    read_pdm_csv,
    write_batches,
)

logger = logging.getLogger(__name__)


@dataclass
class TopologyPlan:
    """Pure, deterministic description of the generated factory structure."""

    # machine node id -> (line id, 1-based position in line)
    placement: dict[str, tuple[str, int]] = field(default_factory=dict)
    line_machines: dict[str, list[str]] = field(default_factory=dict)
    feeds: list[tuple[str, str]] = field(default_factory=list)
    technicians: list[dict[str, str]] = field(default_factory=list)


def plan_topology(machine_ids: list[int], seed: int = C.SEED) -> TopologyPlan:
    ids = sorted(machine_ids)
    base, extra = divmod(len(ids), len(C.LINES))
    plan = TopologyPlan()
    cursor = 0
    for i, (line_id, _plant) in enumerate(C.LINES):
        size = base + (1 if i < extra else 0)  # earlier lines take the remainder
        nodes = [C.machine_node_id(m) for m in ids[cursor : cursor + size]]
        cursor += size
        plan.line_machines[line_id] = nodes
        for pos, node in enumerate(nodes, start=1):
            plan.placement[node] = (line_id, pos)
        plan.feeds += list(zip(nodes, nodes[1:], strict=False))

    for src_line, dst_line in C.CROSS_LINE_FEEDS:
        src, dst = plan.line_machines[src_line], plan.line_machines[dst_line]
        if src and dst:
            plan.feeds.append((src[-1], dst[0]))

    rng = random.Random(seed)
    plan.technicians = [
        {"id": f"T{i:02d}", "name": name, "shift": rng.choice(C.SHIFTS)}
        for i, name in enumerate(C.TECHNICIAN_NAMES, start=1)
    ]
    return plan


def build_topology(driver: Driver, raw_dir: Path = DEFAULT_RAW_DIR) -> dict[str, int]:
    machines = read_pdm_csv(raw_dir, "PdM_machines.csv")
    unknown = set(machines["model"]) - set(C.MODELS)
    if unknown:
        raise IngestionError(f"PdM_machines.csv: unexpected models {sorted(unknown)}")
    if machines["machineID"].duplicated().any():
        raise IngestionError("PdM_machines.csv: duplicate machineID")

    plan = plan_topology([int(m) for m in machines["machineID"]])

    write_batches(
        driver,
        "UNWIND $rows AS row MERGE (p:Plant {id: row.id}) "
        "SET p.name = row.name, p.location = row.location, p.synthetic = true",
        C.PLANTS,
    )
    write_batches(
        driver,
        """
        UNWIND $rows AS row
        MERGE (l:ProductionLine {id: row.id})
        SET l.name = 'Line ' + row.id, l.synthetic = true
        WITH l, row MATCH (p:Plant {id: row.plant})
        MERGE (p)-[:HAS_LINE]->(l)
        """,
        [{"id": lid, "plant": pid} for lid, pid in C.LINES],
    )
    write_batches(
        driver,
        "UNWIND $rows AS row MERGE (m:MachineModel {id: row.id}) "
        "SET m.name = 'Model ' + right(row.id, 1), m.synthetic = true",
        [{"id": m} for m in C.MODELS],
    )
    write_batches(
        driver,
        "UNWIND $rows AS row MERGE (e:ErrorType {id: row.id}) "
        "SET e.description = row.description, e.synthetic = true",
        [{"id": k, "description": v} for k, v in C.ERROR_TYPES.items()],
    )
    write_batches(
        driver,
        "UNWIND $rows AS row MERGE (t:Technician {id: row.id}) "
        "SET t.name = row.name, t.shift = row.shift, t.synthetic = true",
        plan.technicians,
    )

    machine_rows: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []
    sensor_rows: list[dict[str, Any]] = []
    for rec in machines.to_dict("records"):
        node = C.machine_node_id(int(rec["machineID"]))
        line_id, position = plan.placement[node]
        machine_rows.append(
            {
                "id": node,
                "machine_id": int(rec["machineID"]),
                "age": int(rec["age"]),
                "model": rec["model"],
                "line_id": line_id,
                "position": position,
            }
        )
        component_rows += [
            {"id": f"{node}-{ctype}", "machine": node, "type": ctype, "name": name}
            for ctype, name in C.COMPONENTS.items()
        ]
        sensor_rows += [
            {"id": f"{node}-{stype}", "machine": node, "type": stype, "unit": unit}
            for stype, unit in C.SENSOR_UNITS.items()
        ]

    write_batches(
        driver,
        """
        UNWIND $rows AS row
        MERGE (m:Machine {id: row.id})
        ON CREATE SET m.status = 'running'
        SET m.machine_id = row.machine_id, m.age = row.age, m.synthetic = true
        WITH m, row
        MATCH (mm:MachineModel {id: row.model})
        MERGE (m)-[:IS_MODEL]->(mm)
        WITH m, row
        MATCH (l:ProductionLine {id: row.line_id})
        MERGE (l)-[hm:HAS_MACHINE]->(m)
        SET hm.position = row.position
        """,
        machine_rows,
    )
    write_batches(
        driver,
        """
        UNWIND $rows AS row
        MERGE (c:Component {id: row.id})
        SET c.type = row.type, c.name = row.name, c.synthetic = true
        WITH c, row MATCH (m:Machine {id: row.machine})
        MERGE (m)-[:HAS_COMPONENT]->(c)
        """,
        component_rows,
    )
    write_batches(
        driver,
        """
        UNWIND $rows AS row
        MERGE (s:Sensor {id: row.id})
        SET s.type = row.type, s.unit = row.unit, s.synthetic = true
        WITH s, row MATCH (m:Machine {id: row.machine})
        MERGE (m)-[:HAS_SENSOR]->(s)
        """,
        sensor_rows,
    )
    write_batches(
        driver,
        """
        UNWIND $rows AS row
        MATCH (a:Machine {id: row.src}), (b:Machine {id: row.dst})
        MERGE (a)-[:FEEDS_INTO]->(b)
        """,
        [{"src": a, "dst": b} for a, b in plan.feeds],
    )

    counts = {
        "machines": len(machine_rows),
        "components": len(component_rows),
        "sensors": len(sensor_rows),
        "feeds_into": len(plan.feeds),
    }
    logger.info("Topology loaded: %s", counts)
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    driver = create_sync_driver()
    try:
        build_topology(driver)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
