import logging
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from neo4j import Driver

from app.core.config import REPO_ROOT

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw"
DEFAULT_GENERATED_DIR = REPO_ROOT / "data" / "generated"

PDM_COLUMNS = {
    "PdM_machines.csv": ["machineID", "model", "age"],
    "PdM_telemetry.csv": ["datetime", "machineID", "volt", "rotate", "pressure", "vibration"],
    "PdM_errors.csv": ["datetime", "machineID", "errorID"],
    "PdM_maint.csv": ["datetime", "machineID", "comp"],
    "PdM_failures.csv": ["datetime", "machineID", "failure"],
}


class IngestionError(RuntimeError):
    """Raised when input data does not look like what the spec promises (fail loudly)."""


def read_pdm_csv(raw_dir: Path, filename: str) -> pd.DataFrame:
    path = raw_dir / filename
    if not path.exists():
        raise IngestionError(
            f"{path} not found. Download the dataset with:\n"
            "  kaggle datasets download -d arnabbiswas1/microsoft-azure-predictive-maintenance "
            "--unzip -p data/raw"
        )
    df = pd.read_csv(path)
    expected = PDM_COLUMNS[filename]
    if list(df.columns) != expected:
        raise IngestionError(f"{filename}: expected columns {expected}, got {list(df.columns)}")
    return df


def iso_ts(series: pd.Series) -> pd.Series:
    """'2015-01-01 06:00:00' -> '2015-01-01T06:00:00' (accepted by Cypher datetime())."""
    return pd.to_datetime(series).dt.strftime("%Y-%m-%dT%H:%M:%S")


def batched(rows: Sequence[Any], size: int = BATCH_SIZE) -> Iterator[Sequence[Any]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def write_batches(
    driver: Driver, query: str, rows: Sequence[dict[str, Any]], size: int = BATCH_SIZE
) -> None:
    """Run `query` (which must UNWIND $rows) over `rows` in batches, one write tx each."""
    if not rows:
        return
    with driver.session() as session:
        for batch in batched(rows, size):
            session.execute_write(lambda tx, b=batch: tx.run(query, rows=b).consume())  # type: ignore[misc]


def run_write(driver: Driver, query: str, **params: Any) -> None:
    with driver.session() as session:
        session.execute_write(lambda tx: tx.run(query, **params).consume())


def run_read(driver: Driver, query: str, **params: Any) -> list[dict[str, Any]]:
    with driver.session() as session:
        return session.execute_read(lambda tx: [r.data() for r in tx.run(query, **params)])
