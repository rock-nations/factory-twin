"""Demo questions (spec §1 steps 2, 3, 5) through /ws/chat with the real tools and a real graph.

Only the LLMs are scripted; retrieval, Cypher guard, tools, orchestration, trace and streaming
protocol are the production code.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from neo4j import Driver

from app.chat.memory import SessionStore
from app.chat.orchestrator import Orchestrator
from app.chat.service import get_hub
from app.chat.tools import ToolBox
from app.core.config import Settings
from app.core.llm import ConfigError
from app.ingestion.build_topology import build_topology
from app.ingestion.common import run_write
from app.ingestion.embed_documents import embed_documents
from app.ingestion.embed_failures import embed_failures
from app.ingestion.init_schema import init_schema
from app.ingestion.load_events import load_events
from app.main import create_app
from app.models.chat import ProactiveAction, ProactiveEvent
from app.retrieval.graphrag import IncidentFinder
from app.retrieval.text2cypher import Text2Cypher, load_examples
from app.retrieval.vector_rag import KnowledgeBase
from tests.chat_fakes import ScriptedModel
from tests.fakes import HashingEmbedder
from tests.test_embed_integration import DOCS
from tests.test_retrieval_integration import ScriptedLLM

pytestmark = pytest.mark.integration

SETTINGS = Settings(
    _env_file=None,
    openai_cypher_model="cypher-model",
    rag_min_score=0.6,
    price_input_per_m=1.0,
    price_output_per_m=2.0,
)
A2_QUERY = next(e["cypher"] for e in load_examples() if "line A2" in e["question"])


def args(**kw: Any) -> str:
    return json.dumps(kw)


@pytest.fixture
def world(clean_db: Driver, pdm_dir: Path, test_settings: Settings, tmp_path: Path) -> Driver:
    init_schema(clean_db, test_settings)
    build_topology(clean_db, pdm_dir)
    load_events(clean_db, pdm_dir)
    docs = tmp_path / "docs"
    docs.mkdir()
    for name, text in DOCS.items():
        (docs / name).write_text(text)
    emb = HashingEmbedder()
    embed_documents(clean_db, emb, docs)
    embed_failures(clean_db, emb, pdm_dir)
    # M003 sits on line A1 in the fixture (100 machines: 13 per line for the first four lines)
    run_write(
        clean_db,
        """
        MATCH (m:Machine {id: 'M003'})-[:HAS_SENSOR]->(s:Sensor {type: 'vibration'})
        SET m.status = 'alarm', s.latest_value = 70.0, s.warn_high = 55.0, s.crit_high = 62.0
        CREATE (a:Alarm {id: 'AL-1', status: 'open', severity: 'critical', sensor_type: 'vibration',
                         value: 70.0, threshold: 62.0, ts: datetime(), message: 'vibration high',
                         source: 'simulator'})
        CREATE (a)-[:RAISED_ON]->(m)
        """,
    )
    return clean_db


def make_client(
    world: Driver, model: ScriptedModel, cypher_reply: str = "NO_QUERY"
) -> Iterator[TestClient]:
    emb = HashingEmbedder()
    toolbox = ToolBox(
        driver=world,
        text2cypher=Text2Cypher(world, ScriptedLLM(cypher_reply), SETTINGS),
        knowledge_base=KnowledgeBase(world, emb, SETTINGS),
        incidents=IncidentFinder(world, emb),
        cypher_model="cypher-model",
    )
    app = create_app()
    app.state.orchestrator = Orchestrator(model, toolbox, SessionStore(), SETTINGS)
    with TestClient(app) as client:
        yield client


def ask(client: TestClient, message: str, session_id: str = "s1") -> list[dict[str, Any]]:
    events = []
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "user_message", "session_id": session_id, "message": message})
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] in ("done", "error"):
                return events


def test_demo_step2_text2cypher_over_websocket(world: Driver) -> None:
    model = ScriptedModel(
        [[("query_twin_data", args(question="Which machines on line A2 have open alarms?"))], []],
        answer="No machine on A2 has an open alarm [1].",
    )
    for client in make_client(world, model, f"```cypher\n{A2_QUERY}\n```"):
        events = ask(client, "Which machines on Line A2 have open alarms?")
    kinds = [e["type"] for e in events]
    assert kinds[:3] == ["route", "tool_call", "tool_result"] and kinds[-1] == "done"
    assert events[0]["route"] == "cypher"
    result = events[2]
    assert "MATCH" in result["cypher"] and "LIMIT 50" in result["cypher"]
    assert result["row_count"] == 0 and result["summary"] == "no rows"
    trace = events[-1]["trace"]
    assert trace["route"] == "cypher" and trace["tools"][0]["cypher"] == result["cypher"]
    assert trace["cost_usd"] > 0 and trace["total_latency_ms"] >= 0


def test_demo_step3_rag_with_cited_sources(world: Driver) -> None:
    model = ScriptedModel(
        [
            [
                (
                    "search_knowledge_base",
                    args(query="How do I replace a worn spindle bearing?", doc_types=["sop"]),
                )
            ],
            [],
        ],
        answer="Lock out the machine, press out the bearing and fit a new one [1].",
    )
    for client in make_client(world, model):
        events = ask(client, "How do I replace a worn spindle bearing on a model3 machine?")
    assert events[0]["route"] == "rag"
    result = next(e for e in events if e["type"] == "tool_result")
    assert [s["title"] for s in result["sources"]] == ["SOP: Replacing the spindle bearing"]
    assert result["sources"][0]["doc_type"] == "sop" and result["sources"][0]["ref"] == 1
    trace = events[-1]["trace"]
    assert trace["tools"][0]["retrieval_scores"] and "[1]" in events[-1]["answer"]


def test_demo_step5_hybrid_diagnosis_lists_downstream(world: Driver) -> None:
    model = ScriptedModel(
        [
            [
                ("get_machine_context", args(machine_id="M003")),
                ("find_similar_incidents", args(description="vibration high spindle bearing")),
                ("search_knowledge_base", args(query="spindle bearing vibration cause")),
            ],
            [],
        ],
        answer="M003 vibration is critical; downstream M004 [1].",
    )
    for client in make_client(world, model):
        events = ask(
            client, "Machine 3 is vibrating - what's the likely cause and what's affected?"
        )
    assert [e["route"] for e in events if e["type"] == "route"] == ["hybrid"]
    names = [e["name"] for e in events if e["type"] == "tool_call"]
    assert names == ["get_machine_context", "find_similar_incidents", "search_knowledge_base"]
    trace = events[-1]["trace"]
    assert trace["route"] == "hybrid" and len(trace["tools"]) == 3
    # the answer model really received live context, downstream machines and past incidents
    answer_input = model.answer_inputs[0][-1]["content"]
    assert '"state": "critical"' in answer_input and '"downstream"' in answer_input
    assert "F-" in answer_input
    refs = {s["ref"] for t in trace["tools"] for s in t["sources"]}
    assert 1 in refs and len(refs) >= 3


def test_post_chat_non_streaming(world: Driver) -> None:
    model = ScriptedModel([[]], answer="Hi, I can help.")
    for client in make_client(world, model):
        r = client.post("/api/chat", json={"session_id": "p", "message": "hello"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "Hi, I can help." and body["trace"]["route"] == "direct"


def test_post_chat_validates_input(world: Driver) -> None:
    for client in make_client(world, ScriptedModel([[]])):
        assert client.post("/api/chat", json={"session_id": "p", "message": ""}).status_code == 422


def test_tool_error_surfaces_in_trace_not_as_crash(world: Driver) -> None:
    model = ScriptedModel([[("query_twin_data", args(question="delete all"))], []], answer="n/a")
    for client in make_client(world, model, "MATCH (n) DETACH DELETE n"):
        events = ask(client, "delete everything")
    assert events[-1]["type"] == "done"
    assert events[-1]["trace"]["tools"][0]["error"]
    # still there
    assert world.execute_query("MATCH (m:Machine) RETURN count(m) AS n").records[0]["n"] == 100


def test_invalid_ws_messages_get_error_events_and_connection_survives(world: Driver) -> None:
    model = ScriptedModel([[]], answer="ok")
    for client in make_client(world, model):
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"type": "nope"})
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"type": "user_message", "session_id": "s"})
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"type": "user_message", "session_id": "s", "message": "hi"})
            types = []
            while True:
                ev = ws.receive_json()
                types.append(ev["type"])
                if ev["type"] == "done":
                    break
            assert types[0] == "route"


def test_proactive_message_reaches_connected_chats(world: Driver) -> None:
    for client in make_client(world, ScriptedModel([[]])):
        hub = get_hub(client.app)  # type: ignore[arg-type]
        with client.websocket_connect("/ws/chat") as ws:
            assert client.portal is not None
            ev = ProactiveEvent(
                message="⚠️ M017 vibration 3.1σ above normal.",
                machine_id="M017",
                actions=[ProactiveAction(label="Diagnose M017", message="diagnose M017")],
            )
            delivered = client.portal.call(hub.broadcast, ev)
            got = ws.receive_json()
        assert delivered == 1
        assert got["type"] == "proactive" and got["actions"][0]["message"] == "diagnose M017"
        assert len(hub) == 0  # unregistered on disconnect


def test_chat_unavailable_without_configuration(
    world: Driver, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> Orchestrator:
        raise ConfigError("OPENAI_API_KEY is not set; add it to .env")

    monkeypatch.setattr("app.chat.service.build_orchestrator", boom)
    with TestClient(create_app()) as client:
        r = client.post("/api/chat", json={"session_id": "p", "message": "hi"})
        assert r.status_code == 503 and "OPENAI_API_KEY" in r.json()["detail"]
        events = ask(client, "hi")
        assert events == [{"type": "error", "message": "OPENAI_API_KEY is not set; add it to .env"}]
