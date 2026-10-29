"""Try a retrieval tool from the command line.

python -m app.retrieval.try "Which machines on line A2 have open alarms?"
python -m app.retrieval.try --tool rag "How do I replace a spindle bearing?"
python -m app.retrieval.try --tool context M017
python -m app.retrieval.try --tool similar "vibration rising on a model3 machine"
"""

import argparse
import json

from pydantic import BaseModel

from app.core.config import get_settings
from app.core.embeddings import OpenAIEmbedder
from app.core.llm import LLM
from app.core.neo4j import create_sync_driver
from app.retrieval.graphrag import IncidentFinder
from app.retrieval.machine_context import get_machine_context
from app.retrieval.text2cypher import Text2Cypher
from app.retrieval.vector_rag import KnowledgeBase


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("text", help="question / query / machine id")
    p.add_argument("--tool", choices=["cypher", "rag", "context", "similar"], default="cypher")
    p.add_argument("--model", help="machine_model filter (rag, similar)")
    args = p.parse_args()

    driver = create_sync_driver()
    out: BaseModel
    try:
        if args.tool == "cypher":
            out = Text2Cypher(driver, LLM(get_settings())).query(args.text)
            print(out.cypher)
        elif args.tool == "rag":
            out = KnowledgeBase(driver, OpenAIEmbedder()).search(
                args.text, machine_model=args.model
            )
        elif args.tool == "similar":
            out = IncidentFinder(driver, OpenAIEmbedder()).find(args.text, args.model)
        else:
            print(json.dumps(get_machine_context(driver, args.text), indent=2, default=str))
            return
        print(out.model_dump_json(indent=2, exclude_none=True))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
