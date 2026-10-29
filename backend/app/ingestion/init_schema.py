import logging
import time
from pathlib import Path

from neo4j import Driver

from app.core.config import Settings, get_settings
from app.core.neo4j import create_sync_driver

logger = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "graph" / "schema.cypher"


def load_statements(embedding_dimensions: int) -> list[str]:
    text = SCHEMA_FILE.read_text().replace("{{EMBEDDING_DIMENSIONS}}", str(embedding_dimensions))
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("//")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def wait_for_indexes(driver: Driver, timeout_s: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        with driver.session() as session:
            states = {r["name"]: r["state"] for r in session.run("SHOW INDEXES YIELD name, state")}
        pending = {n: s for n, s in states.items() if s != "ONLINE"}
        if not pending:
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"Indexes not ONLINE after {timeout_s}s: {pending}")
        time.sleep(1)


def init_schema(driver: Driver, settings: Settings | None = None) -> int:
    s = settings or get_settings()
    statements = load_statements(s.openai_embedding_dimensions)
    with driver.session() as session:
        for stmt in statements:
            session.run(stmt).consume()
    wait_for_indexes(driver)
    logger.info("Applied %d schema statements; indexes ONLINE", len(statements))
    return len(statements)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    driver = create_sync_driver()
    try:
        init_schema(driver)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
