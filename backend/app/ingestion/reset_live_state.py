"""Reset the twin for a clean demo (spec §6). Structure, Kaggle history, documents and embeddings
are never touched.

    python -m app.ingestion.reset_live_state
"""

import logging
from typing import Any

from neo4j import Driver

from app.core.neo4j import create_sync_driver

logger = logging.getLogger(__name__)

# Delete in chunks so a long simulator session does not build one huge transaction.
DELETE_BATCH = 5000


def _delete_all(driver: Driver, match: str) -> int:
    total = 0
    with driver.session() as session:
        while True:
            deleted: int = session.execute_write(
                lambda tx: tx.run(
                    f"{match} WITH n LIMIT {DELETE_BATCH} DETACH DELETE n RETURN count(*) AS c"
                ).single()["c"]  # type: ignore[index]
            )
            total += deleted
            if deleted < DELETE_BATCH:
                return total


def reset_live_state(driver: Driver) -> dict[str, Any]:
    alarms = _delete_all(driver, "MATCH (n:Alarm)")
    errors = _delete_all(driver, "MATCH (n:ErrorEvent {source: 'simulator'})")
    with driver.session() as session:
        machines = session.execute_write(
            lambda tx: tx.run(
                "MATCH (m:Machine) SET m.status = 'running' RETURN count(m) AS c"
            ).single()["c"]  # type: ignore[index]
        )
        sensors = session.execute_write(
            lambda tx: tx.run(
                "MATCH (s:Sensor) "
                "REMOVE s.latest_value, s.latest_ts, s.avg_24h RETURN count(s) AS c"
            ).single()["c"]  # type: ignore[index]
        )
    summary = {
        "alarms_deleted": alarms,
        "simulator_errors_deleted": errors,
        "machines_reset": machines,
        "sensors_cleared": sensors,
    }
    logger.info("Live state reset: %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    driver = create_sync_driver()
    try:
        print(reset_live_state(driver))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
