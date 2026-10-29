import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from neo4j import Driver, GraphDatabase

from app.core.config import Settings
from tests.pdm_fixture import write_pdm_fixture

# tests must never start the live consumer against the dev database
os.environ["ENABLE_STREAMING"] = "0"

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

TEST_NEO4J_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7688")
TEST_NEO4J_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "test-password")
TEST_MQTT_PORT = int(os.environ.get("MQTT_TEST_PORT", "1884"))


@pytest.fixture(scope="session")
def pdm_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("pdm")
    write_pdm_fixture(out)
    return out


@pytest.fixture(scope="session")
def neo4j_test_driver() -> Iterator[Driver]:
    """Throwaway Neo4j (docker compose --profile test up -d neo4j-test). Never the dev DB."""
    driver = GraphDatabase.driver(TEST_NEO4J_URI, auth=("neo4j", TEST_NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
    except Exception as exc:
        driver.close()
        pytest.skip(f"test Neo4j not reachable at {TEST_NEO4J_URI}: {exc}")
    yield driver
    driver.close()


@pytest.fixture
def clean_db(neo4j_test_driver: Driver) -> Driver:
    with neo4j_test_driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
    return neo4j_test_driver


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(_env_file=None, neo4j_uri=TEST_NEO4J_URI, neo4j_password=TEST_NEO4J_PASSWORD)
