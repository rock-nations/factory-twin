from pathlib import Path

import pandas as pd
import pytest
from neo4j import Driver

from app.core.config import Settings
from app.graph import constants as C
from app.ingestion.build_topology import build_topology
from app.ingestion.common import IngestionError, run_read
from app.ingestion.compute_thresholds import compute_and_store_thresholds
from app.ingestion.init_schema import init_schema
from app.ingestion.load_events import load_events
from app.ingestion.run_all import graph_counts

pytestmark = pytest.mark.integration


@pytest.fixture
def loaded(clean_db: Driver, pdm_dir: Path, test_settings: Settings, tmp_path: Path) -> Driver:
    init_schema(clean_db, test_settings)
    build_topology(clean_db, pdm_dir)
    load_events(clean_db, pdm_dir)
    compute_and_store_thresholds(clean_db, pdm_dir, tmp_path)
    return clean_db


def test_idempotent_rerun_gives_identical_counts(
    loaded: Driver, pdm_dir: Path, test_settings: Settings, tmp_path: Path
) -> None:
    before = graph_counts(loaded)
    init_schema(loaded, test_settings)
    build_topology(loaded, pdm_dir)
    load_events(loaded, pdm_dir)
    compute_and_store_thresholds(loaded, pdm_dir, tmp_path)
    assert graph_counts(loaded) == before


def test_constraints_and_indexes_present(loaded: Driver) -> None:
    names = {r["name"] for r in run_read(loaded, "SHOW CONSTRAINTS YIELD name RETURN name")}
    assert {"plant_id", "machine_id", "chunk_id", "production_line_id"} <= names
    idx = {r["name"]: r["state"] for r in run_read(loaded, "SHOW INDEXES YIELD name, state")}
    assert idx["chunk_embedding"] == idx["failure_embedding"] == idx["chunk_fulltext"] == "ONLINE"


def test_every_machine_has_four_sensors_and_four_components(loaded: Driver) -> None:
    rows = run_read(
        loaded,
        """
        MATCH (m:Machine)
        RETURN m.id AS id,
               count{ (m)-[:HAS_SENSOR]->() } AS s, count{ (m)-[:HAS_COMPONENT]->() } AS c,
               count{ (m)<-[:HAS_MACHINE]-() } AS lines, count{ (m)-[:IS_MODEL]->() } AS models
        """,
    )
    assert len(rows) == 100
    assert all(r["s"] == 4 and r["c"] == 4 and r["lines"] == 1 and r["models"] == 1 for r in rows)


def test_structure_counts(loaded: Driver) -> None:
    nodes, _ = graph_counts(loaded)
    assert nodes["Plant"] == 2 and nodes["ProductionLine"] == 8
    assert nodes["Technician"] == 10 and nodes["ErrorType"] == 5 and nodes["MachineModel"] == 4


def test_feeds_into_has_no_cycles_within_a_line(loaded: Driver) -> None:
    rows = run_read(
        loaded,
        "MATCH (l:ProductionLine)-[:HAS_MACHINE]->(m) "
        "WHERE (m)-[:FEEDS_INTO*1..]->(m) RETURN l.id AS line",
    )
    assert rows == []


def test_failures_are_linked_and_resolved(loaded: Driver, pdm_dir: Path) -> None:
    n_fail = len(pd.read_csv(pdm_dir / "PdM_failures.csv").drop_duplicates())
    rows = run_read(
        loaded,
        """
        MATCH (f:Failure)
        RETURN count(f) AS n, count{ (f)-[:FAILED]->(:Component) } AS comp_links,
               sum(count{ (f)-[:RESOLVED_BY]->(:MaintenanceEvent {reason: 'failure'}) }) AS resolved
        """,
    )
    assert rows[0]["n"] == n_fail
    assert rows[0]["resolved"] == n_fail


def test_event_nodes_are_tagged_kaggle(loaded: Driver) -> None:
    rows = run_read(
        loaded,
        "MATCH (n) WHERE n:ErrorEvent OR n:MaintenanceEvent OR n:Failure "
        "RETURN count(n) AS n, "
        "sum(CASE WHEN n.source = 'kaggle' THEN 1 ELSE 0 END) AS tagged",
    )
    assert rows[0]["n"] == rows[0]["tagged"] > 0


def test_thresholds_are_on_all_sensors(loaded: Driver) -> None:
    rows = run_read(
        loaded,
        "MATCH (s:Sensor) "
        "RETURN s.type AS t, s.warn_high AS wh, s.warn_low AS wl, s.crit_high AS ch",
    )
    assert len(rows) == 400
    assert all(r["wh"] is not None and r["ch"] > r["wh"] for r in rows)
    assert all((r["wl"] is not None) == (r["t"] in C.SENSORS_WITH_LOW_THRESHOLDS) for r in rows)


def test_component_last_replaced_at_set(loaded: Driver) -> None:
    rows = run_read(
        loaded,
        "MATCH (c:Component)<-[:REPLACED]-() RETURN count(DISTINCT c) AS n, "
        "count(DISTINCT CASE WHEN c.last_replaced_at IS NOT NULL THEN c END) AS set_",
    )
    assert rows[0]["n"] == rows[0]["set_"] > 0


def test_llm_schema_covers_labels_and_rels_in_db(loaded: Driver) -> None:
    from tests.test_schema_files import LLM_SCHEMA

    labels = {r["l"] for r in run_read(loaded, "CALL db.labels() YIELD label AS l RETURN l")}
    rels = {
        r["t"]
        for r in run_read(
            loaded, "CALL db.relationshipTypes() YIELD relationshipType AS t RETURN t"
        )
    }
    assert all(f":{label}" in LLM_SCHEMA for label in labels)
    assert all(f":{rel}" in LLM_SCHEMA for rel in rels)


def test_missing_file_fails_loudly(clean_db: Driver, tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="kaggle datasets download"):
        build_topology(clean_db, tmp_path)


def test_wrong_columns_fail_loudly(clean_db: Driver, pdm_dir: Path, tmp_path: Path) -> None:
    pd.DataFrame({"machineID": [1], "model": ["model1"]}).to_csv(
        tmp_path / "PdM_machines.csv", index=False
    )
    with pytest.raises(IngestionError, match="expected columns"):
        build_topology(clean_db, tmp_path)
