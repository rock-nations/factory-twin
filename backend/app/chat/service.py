"""Wires the real orchestrator (lazily, so the API starts without an OpenAI key)."""

import threading

from fastapi import FastAPI

from app.chat.hub import ChatHub
from app.chat.model import OpenAIChatModel
from app.chat.orchestrator import Orchestrator
from app.chat.tools import ToolBox
from app.core.config import Settings, get_settings
from app.core.embeddings import OpenAIEmbedder
from app.core.llm import LLM
from app.core.neo4j import get_sync_driver
from app.retrieval.graphrag import IncidentFinder
from app.retrieval.text2cypher import Text2Cypher
from app.retrieval.vector_rag import KnowledgeBase

_lock = threading.Lock()


def build_orchestrator(settings: Settings | None = None) -> Orchestrator:
    s = settings or get_settings()
    driver = get_sync_driver()
    embedder = OpenAIEmbedder(s)
    toolbox = ToolBox(
        driver=driver,
        text2cypher=Text2Cypher(driver, LLM(s), s),
        knowledge_base=KnowledgeBase(driver, embedder, s),
        incidents=IncidentFinder(driver, embedder),
        cypher_model=s.openai_cypher_model,
    )
    return Orchestrator(OpenAIChatModel(s), toolbox, settings=s)


def get_hub(app: FastAPI) -> ChatHub:
    hub: ChatHub | None = getattr(app.state, "chat_hub", None)
    if hub is None:
        hub = app.state.chat_hub = ChatHub()
    return hub


def get_orchestrator(app: FastAPI) -> Orchestrator:
    """Built on first use; tests may preset `app.state.orchestrator`."""
    orch: Orchestrator | None = getattr(app.state, "orchestrator", None)
    if orch is None:
        with _lock:
            orch = getattr(app.state, "orchestrator", None)
            if orch is None:
                orch = app.state.orchestrator = build_orchestrator()
    return orch
