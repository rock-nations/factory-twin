# CLAUDE.md — FactoryTwin Copilot

Guidance for Claude Code working in this repository. The full specification lives in
`docs/PROJECT_SPEC.md` — read it before starting any phase.

## What this project is
A portfolio project: a real-time **Smart Factory Digital Twin** stored in **Neo4j**, with a
**chatbot** that answers questions by
1. generating **Cypher** from natural language (Text2Cypher) for structured/live data,
2. **vector search + RAG** over manuals, troubleshooting guides and incident reports,
3. **hybrid GraphRAG** (vector search → graph traversal) for diagnostic questions,
and explains results in natural language. Sensor data streams in live via MQTT.

## Stack
- **Backend:** Python 3.12, FastAPI, uv, `neo4j` driver, `neo4j-graphrag[openai]`, `openai` SDK,
  `aiomqtt`, pydantic v2 / pydantic-settings, pytest, ruff, mypy
- **Frontend:** Angular (latest stable; standalone components, signals, new control flow),
  Angular Material, Cytoscape.js (graph view), ngx-echarts (charts), ngx-markdown (chat)
- **Infra (Docker Compose):** Neo4j Community Edition (+APOC), Eclipse Mosquitto
- **LLM/embeddings:** OpenAI API (model names come from `.env`, never hardcoded)

## Repository layout
```
backend/      FastAPI app, ingestion scripts, retrieval, simulator, tests, eval
frontend/     Angular app
docs/         PROJECT_SPEC.md and other docs
data/raw/     Kaggle CSVs (git-ignored)
data/generated/ generated documents (committed — small, reproducible)
docker-compose.yml, .env.example, README.md
```

## Common commands
```bash
docker compose up -d                       # Neo4j (7474/7687) + Mosquitto (1883)
cd backend && uv sync                      # install backend deps
uv run python -m app.ingestion.run_all     # full data load (idempotent)
uv run uvicorn app.main:app --reload       # backend on :8000
uv run python -m app.streaming.simulator   # start sensor simulator
uv run pytest                              # backend tests
uv run ruff check . && uv run mypy app     # lint + types
uv run python -m eval.run_eval             # evaluation report
cd frontend && npm install && npm start    # Angular dev server on :4200 (proxies /api, /ws)
```

## How to work in this repo
- Work **phase by phase** as defined in `docs/PROJECT_SPEC.md` §12. Finish one phase, make its
  acceptance criteria pass, update the checklist in §12, then **stop and summarize** for review.
- Before building anything that touches Neo4j, check the graph model in spec §5. Do not invent
  labels, relationship types or properties — extend §5 first if something is genuinely missing.
- Prefer small, reviewable commits with clear messages.
- When a library API is uncertain (neo4j-graphrag, OpenAI SDK, Angular), check the installed
  version's docs/source rather than guessing.

## Hard rules
- **Never commit secrets.** `.env` is git-ignored; keep `.env.example` up to date.
- **Text2Cypher is read-only.** LLM-generated Cypher must pass `cypher_guard` and run in a
  READ transaction. Never execute generated Cypher in a write transaction.
- **Never hardcode model names** — use settings (`OPENAI_*_MODEL`).
- **Raw telemetry stays out of Neo4j.** The graph holds the latest value + small aggregates only.
- **Mark synthetic data.** Every generated node/document gets `synthetic: true`.
- **Idempotent ingestion.** Use `MERGE` with uniqueness constraints; re-running must not duplicate.
- **Cost awareness.** Log tokens + estimated cost per chat request; keep prompts' static prefix
  stable (prompt caching); cache embeddings by content hash.
- Run tests + ruff before declaring a task done. Don't mark a phase complete with failing tests.

## Git workflow
- **Do not run `git commit` or `git push`.** The repository owner reviews and commits all changes.
- At the end of each task, propose a commit message in Conventional Commits format
  (e.g. `feat(retrieval): add cypher guard with read-only execution`) and list the files changed.
- Never add `Co-Authored-By`, "Generated with", session links or any other trailer to commit
  messages or PR descriptions.
- Keep changes per task small enough to be one logical commit.

## Code style
- Python: type hints everywhere, pydantic models at API boundaries, async FastAPI handlers,
  one Neo4j driver per process (`app/core/neo4j.py`), logging via `logging` (no prints).
- Angular: standalone components only, signals for state, `inject()` over constructor DI,
  typed models in `core/models`, RxJS only for streams (WebSockets, HTTP).
- Keep prompts in `backend/app/prompts/*.md` (not inline strings) so they're reviewable.
