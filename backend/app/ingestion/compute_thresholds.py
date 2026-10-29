"""Sensor thresholds from the full telemetry CSV (spec §5.4)."""

import json
import logging
from pathlib import Path

import pandas as pd
from neo4j import Driver

from app.core.neo4j import create_sync_driver
from app.graph import constants as C
from app.ingestion.common import (
    DEFAULT_GENERATED_DIR,
    DEFAULT_RAW_DIR,
    read_pdm_csv,
    write_batches,
)

logger = logging.getLogger(__name__)

WARN_SIGMA = 2.5
CRIT_SIGMA = 3.5


def thresholds_from_stats(sensor: str, mean: float, std: float) -> dict[str, float | None]:
    has_low = sensor in C.SENSORS_WITH_LOW_THRESHOLDS
    return {
        "mean": mean,
        "std": std,
        "warn_high": mean + WARN_SIGMA * std,
        "crit_high": mean + CRIT_SIGMA * std,
        "warn_low": mean - WARN_SIGMA * std if has_low else None,
        "crit_low": mean - CRIT_SIGMA * std if has_low else None,
    }


def compute_thresholds(telemetry: pd.DataFrame) -> dict[str, dict[str, float | None]]:
    return {
        s: thresholds_from_stats(s, float(telemetry[s].mean()), float(telemetry[s].std()))
        for s in C.SENSOR_UNITS
    }


def compute_and_store_thresholds(
    driver: Driver,
    raw_dir: Path = DEFAULT_RAW_DIR,
    generated_dir: Path = DEFAULT_GENERATED_DIR,
) -> dict[str, dict[str, float | None]]:
    thresholds = compute_thresholds(read_pdm_csv(raw_dir, "PdM_telemetry.csv"))
    generated_dir.mkdir(parents=True, exist_ok=True)
    (generated_dir / "thresholds.json").write_text(json.dumps(thresholds, indent=2) + "\n")
    write_batches(
        driver,
        """
        UNWIND $rows AS row
        MATCH (s:Sensor {type: row.type})
        SET s.warn_high = row.warn_high, s.crit_high = row.crit_high,
            s.warn_low = row.warn_low, s.crit_low = row.crit_low
        """,
        [{"type": s, **t} for s, t in thresholds.items()],
    )
    logger.info("Thresholds stored for %s", list(thresholds))
    return thresholds


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    driver = create_sync_driver()
    try:
        compute_and_store_thresholds(driver)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
