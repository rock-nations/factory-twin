"""Historical errors, maintenance and failures from the Kaggle CSVs (all tagged source='kaggle')."""

import logging
import random
from pathlib import Path
from typing import Any

import pandas as pd
from neo4j import Driver

from app.core.neo4j import create_sync_driver
from app.graph import constants as C
from app.ingestion.common import (
    DEFAULT_RAW_DIR,
    IngestionError,
    iso_ts,
    read_pdm_csv,
    run_write,
    write_batches,
)

logger = logging.getLogger(__name__)


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame -> plain dicts for Cypher params (NaN becomes None)."""
    return [
        {str(k): (None if pd.isna(v) else v) for k, v in rec.items()}
        for rec in df.to_dict("records")
    ]


def _machine_col(df: pd.DataFrame) -> pd.Series:
    return df["machineID"].map(lambda m: C.machine_node_id(int(m)))


def _compact(ts: pd.Series) -> pd.Series:
    return ts.str.replace(r"[-:T]", "", regex=True)


def error_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = pd.DataFrame(
        {"ts": iso_ts(df["datetime"]), "machine": _machine_col(df), "error": df["errorID"]}
    )
    out["id"] = "ERR-" + out["machine"] + "-" + _compact(out["ts"]) + "-" + out["error"]
    return _records(out.drop_duplicates("id"))


def maintenance_rows(
    maint: pd.DataFrame, failures: pd.DataFrame, seed: int = C.SEED
) -> list[dict[str, Any]]:
    out = pd.DataFrame(
        {"ts": iso_ts(maint["datetime"]), "machine": _machine_col(maint), "comp": maint["comp"]}
    )
    out["id"] = "MNT-" + out["machine"] + "-" + _compact(out["ts"]) + "-" + out["comp"]
    out = out.drop_duplicates("id").sort_values(["ts", "machine", "comp"]).reset_index(drop=True)

    failed = set(
        "MNT-" + _machine_col(failures) + "-" + _compact(iso_ts(failures["datetime"]))
        + "-" + failures["failure"]
    )  # fmt: skip
    out["reason"] = out["id"].map(lambda i: "failure" if i in failed else "scheduled")
    out["component"] = out["machine"] + "-" + out["comp"]

    # round-robin over a seeded shuffle of technicians -> deterministic assignment
    techs = [f"T{i:02d}" for i in range(1, len(C.TECHNICIAN_NAMES) + 1)]
    random.Random(seed).shuffle(techs)
    out["technician"] = [techs[i % len(techs)] for i in range(len(out))]
    return _records(out)


def failure_rows(failures: pd.DataFrame, maint_ids: set[str]) -> list[dict[str, Any]]:
    out = pd.DataFrame(
        {
            "ts": iso_ts(failures["datetime"]),
            "machine": _machine_col(failures),
            "comp": failures["failure"],
        }
    )
    out = out.drop_duplicates().sort_values(["ts", "machine", "comp"]).reset_index(drop=True)
    out["id"] = [f"F-{i:04d}" for i in range(1, len(out) + 1)]
    out["component"] = out["machine"] + "-" + out["comp"]
    maint_id = "MNT-" + out["machine"] + "-" + _compact(out["ts"]) + "-" + out["comp"]
    out["maint_id"] = maint_id.where(maint_id.isin(maint_ids), None)
    out["description"] = out.apply(
        lambda r: f"{C.COMPONENTS[r['comp']]} failure on {r['machine']} at {r['ts']}", axis=1
    )
    return _records(out)


def load_events(driver: Driver, raw_dir: Path = DEFAULT_RAW_DIR) -> dict[str, int]:
    errors = read_pdm_csv(raw_dir, "PdM_errors.csv")
    maint = read_pdm_csv(raw_dir, "PdM_maint.csv")
    failures = read_pdm_csv(raw_dir, "PdM_failures.csv")
    for name, series, allowed in [
        ("errorID", errors["errorID"], C.ERROR_TYPES),
        ("comp", maint["comp"], C.COMPONENTS),
        ("failure", failures["failure"], C.COMPONENTS),
    ]:
        bad = set(series) - set(allowed)
        if bad:
            raise IngestionError(f"unexpected {name} values: {sorted(bad)}")

    err = error_rows(errors)
    mnt = maintenance_rows(maint, failures)
    fail = failure_rows(failures, {r["id"] for r in mnt})

    write_batches(
        driver,
        """
        UNWIND $rows AS row
        MERGE (e:ErrorEvent {id: row.id})
        SET e.ts = datetime(row.ts), e.source = 'kaggle'
        WITH e, row MATCH (m:Machine {id: row.machine})
        MERGE (e)-[:OCCURRED_ON]->(m)
        WITH e, row MATCH (t:ErrorType {id: row.error})
        MERGE (e)-[:OF_TYPE]->(t)
        """,
        err,
    )
    write_batches(
        driver,
        """
        UNWIND $rows AS row
        MERGE (e:MaintenanceEvent {id: row.id})
        SET e.ts = datetime(row.ts), e.kind = 'replacement', e.reason = row.reason,
            e.source = 'kaggle'
        WITH e, row MATCH (m:Machine {id: row.machine})
        MERGE (e)-[:PERFORMED_ON]->(m)
        WITH e, row MATCH (c:Component {id: row.component})
        MERGE (e)-[:REPLACED]->(c)
        WITH e, row MATCH (t:Technician {id: row.technician})
        MERGE (e)-[:PERFORMED_BY]->(t)
        """,
        mnt,
    )
    write_batches(
        driver,
        """
        UNWIND $rows AS row
        MERGE (f:Failure {id: row.id})
        ON CREATE SET f.description = row.description
        SET f.ts = datetime(row.ts), f.source = 'kaggle'
        WITH f, row MATCH (m:Machine {id: row.machine})
        MERGE (f)-[:OCCURRED_ON]->(m)
        WITH f, row MATCH (c:Component {id: row.component})
        MERGE (f)-[:FAILED]->(c)
        WITH f, row
        OPTIONAL MATCH (me:MaintenanceEvent {id: row.maint_id})
        FOREACH (_ IN CASE WHEN me IS NULL THEN [] ELSE [1] END |
            MERGE (f)-[:RESOLVED_BY]->(me))
        """,
        fail,
    )
    run_write(
        driver,
        """
        MATCH (c:Component)<-[:REPLACED]-(e:MaintenanceEvent)
        WITH c, max(e.ts) AS last
        SET c.last_replaced_at = last
        """,
    )

    counts = {"errors": len(err), "maintenance": len(mnt), "failures": len(fail)}
    logger.info("Events loaded: %s", counts)
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    driver = create_sync_driver()
    try:
        load_events(driver)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
