"""Natural language -> guarded read-only Cypher, with one error-feedback retry (spec §8.2)."""

import logging
import re
from functools import lru_cache
from pathlib import Path

import yaml
from neo4j import Driver

from app.core.config import Settings, get_settings
from app.core.llm import TextCompleter, Usage, require_setting
from app.models.retrieval import QueryResult, Text2CypherResult
from app.retrieval.cypher_guard import run_guarded

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
SCHEMA_FILE = Path(__file__).resolve().parents[1] / "graph" / "schema_for_llm.md"
EXAMPLES_FILE = Path(__file__).with_name("cypher_examples.yaml")

_FENCE = re.compile(r"```(?:cypher)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
NO_QUERY = "NO_QUERY"


@lru_cache
def load_examples() -> list[dict[str, str]]:
    examples = yaml.safe_load(EXAMPLES_FILE.read_text())
    return [{"question": e["question"].strip(), "cypher": e["cypher"].strip()} for e in examples]


@lru_cache
def build_system_prompt() -> str:
    """Stable prefix (instructions + schema + examples): identical across requests for caching."""
    shots = "\n\n".join(
        f"Question: {e['question']}\n```cypher\n{e['cypher']}\n```" for e in load_examples()
    )
    return (
        f"{(PROMPTS_DIR / 'text2cypher_system.md').read_text().strip()}\n\n"
        f"# Schema\n\n{SCHEMA_FILE.read_text().strip()}\n\n# Examples\n\n{shots}\n"
    )


def extract_cypher(text: str) -> str:
    m = _FENCE.search(text)
    return (m.group(1) if m else text).strip()


class Text2Cypher:
    def __init__(
        self, driver: Driver, llm: TextCompleter, settings: Settings | None = None
    ) -> None:
        self.driver = driver
        self.llm = llm
        self.settings = settings or get_settings()

    def query(self, question: str, context: str = "") -> Text2CypherResult:
        """`context` is optional extra info for the model (e.g. entities from earlier turns)."""
        model = require_setting(self.settings.openai_cypher_model, "OPENAI_CYPHER_MODEL")
        user = f"{context.strip()}\n\nQuestion: {question}".strip() if context else question
        messages: list[dict[str, str]] = [{"role": "user", "content": user}]
        usage = Usage()
        result = QueryResult(cypher="")
        attempts = 0
        for attempts in (1, 2):
            text, u = self.llm.complete(
                model, build_system_prompt(), messages, max_output_tokens=3000
            )
            usage += u
            cypher = extract_cypher(text)
            if cypher.upper() == NO_QUERY:
                result = QueryResult(
                    cypher="", error="This question cannot be answered from the graph data."
                )
                break
            result = run_guarded(self.driver, cypher)
            if result.error is None:
                break
            logger.info("Text2Cypher attempt %d failed: %s", attempts, result.error)
            messages += [
                {"role": "assistant", "content": f"```cypher\n{cypher}\n```"},
                {
                    "role": "user",
                    "content": f"That query failed with: {result.error}\n"
                    "Return a corrected query in a ```cypher block.",
                },
            ]
        return Text2CypherResult(
            **result.model_dump(),
            question=question,
            attempts=attempts,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
