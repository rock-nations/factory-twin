# FactoryTwin Copilot — Project Specification

> **Audience:** Claude Code (and the human owner). This document is the single source of truth
> for what to build. Work through §12 phase by phase. Each phase ends with acceptance criteria.

---

## 1. Goal & demo story

Build a **real-time Smart Factory Digital Twin** in Neo4j and a **chatbot copilot** that lets an
engineer talk to the factory in natural language.

**The 3-minute demo this project must support:**
1. Dashboard shows 2 plants, ~8 production lines, 100 machines, live sensor values updating.
2. User asks: *"Which machines on Line A2 have open alarms?"* → bot generates Cypher, shows it,
   answers in plain English with a small results table. (**Text2Cypher**)
3. User asks: *"How do I replace a worn spindle bearing on a model3 machine?"* → bot answers from
   the manual with cited sources. (**Vector search + RAG**)
4. Presenter clicks **Inject anomaly** on Machine 17 (vibration). Within seconds an alarm toast
   appears and the chat proactively posts a warning. (**Real-time**)
5. User asks: *"Machine 17 is vibrating — what's the likely cause and what's affected
   downstream?"* → bot combines live readings, maintenance history, similar past incidents and
   the manual, and lists downstream machines via `FEEDS_INTO`. (**Hybrid GraphRAG**)
6. Every answer has a collapsible **"How I answered"** panel: route, tools used, Cypher, sources,
   latency, tokens and estimated cost.

**Non-goals:** auth/multi-tenancy, production deployment, ML failure prediction (possible later).

---

## 2. Architecture

```
┌─────────────────────────── Angular frontend (:4200) ───────────────────────────┐
│ Dashboard │ Machine detail │ Graph explorer │ Simulator panel │ Chat drawer    │
└──────────────┬──────────────────────────────┬─────────────────────────────────┘
               │ REST /api/*                  │ WebSocket /ws/chat, /ws/events
┌──────────────▼──────────────────────────────▼─────────────────────────────────┐
│ FastAPI backend (:8000)                                                        │
│  api/        REST + WS endpoints                                               │
│  chat/       orchestrator (agent loop w/ OpenAI tool calling), answer streaming│
│  retrieval/  text2cypher (+cypher_guard), vector RAG, GraphRAG, similar incid. │
│  streaming/  MQTT consumer (background task), alarm engine, WS broadcaster     │
│  services/   graph queries for REST (assets, alarms, graph view)               │
└───────┬───────────────────────────────────────────────▲───────────────────────┘
        │ Bolt                                          │ MQTT subscribe
┌───────▼─────────────┐                    ┌────────────┴──────────┐
│ Neo4j Community     │                    │ Mosquitto (:1883)     │
│ graph + vector +    │                    └────────────▲──────────┘
│ fulltext indexes    │                                 │ MQTT publish
└─────────────────────┘                    ┌────────────┴──────────┐
                                           │ Simulator (separate   │
                                           │ process) replays CSV  │
                                           └───────────────────────┘
```

**Process model**
- `docker compose`: Neo4j + Mosquitto only.
- Backend runs locally with `uv run uvicorn` (fast reload on macOS). The **MQTT consumer runs
  inside the FastAPI process** as a lifespan background task so it can push WS events directly.
- Simulator runs as a separate Python process (`python -m app.streaming.simulator`) and is also
  controllable through REST (start/stop/speed/inject) via a small MQTT control topic.

---

## 3. Prerequisites (macOS)

```bash
brew install python@3.12 uv git node        # node = current LTS, needed for Angular
brew install --cask docker                   # or: brew install orbstack
uv tool install kaggle                       # needs ~/.kaggle/kaggle.json (free Kaggle account)
```
- Docker: allocate ≥ 4 GB RAM.
- OpenAI: API key from platform.openai.com with prepaid credits (a ChatGPT subscription does not
  include API usage). Set a monthly spending limit in the OpenAI dashboard.

---

## 4. Data sources

### 4.1 Kaggle — Microsoft Azure Predictive Maintenance
```bash
kaggle datasets download -d arnabbiswas1/microsoft-azure-predictive-maintenance --unzip -p data/raw
```
Expected files and columns (**verify on load; fail loudly if different**):

| File | Columns | Notes |
|---|---|---|
| `PdM_machines.csv` | machineID, model, age | 100 machines, model1–model4 |
| `PdM_telemetry.csv` | datetime, machineID, volt, rotate, pressure, vibration | hourly, year 2015, ~876k rows |
| `PdM_errors.csv` | datetime, machineID, errorID | error1–error5, non-fatal |
| `PdM_maint.csv` | datetime, machineID, comp | component replacements comp1–comp4 |
| `PdM_failures.csv` | datetime, machineID, failure | failures (subset of maint) |

Data is synthetic by origin — say so in the README.

### 4.2 Generated factory topology (deterministic, seeded)
The dataset has no plant/line structure. `build_topology.py` (seed=42) creates:
- 2 **Plants**: `P1` "Munich Assembly", `P2` "Leipzig Machining" (names arbitrary)
- 8 **ProductionLines**: `A1–A4` (P1), `B1–B4` (P2); machines distributed in ID order
  (12–13 per line)
- `FEEDS_INTO` chain inside each line in sequence order, plus 3 cross-line feeds
  (e.g. last machine of A2 → first of A3) to make downstream analysis interesting
- 4 **Components** per machine (`comp1..comp4`) with human-readable names:
  comp1 "Spindle bearing", comp2 "Hydraulic pump", comp3 "Drive motor", comp4 "Coolant valve"
  (keep this mapping in one constant; documents must use the same names)
- 4 **Sensors** per machine: volt, rotate, pressure, vibration, with units V, rpm, bar, mm/s
- 10 **Technicians** (synthetic names), maintenance events assigned round-robin with seed
- All generated entities get `synthetic: true`

### 4.3 Generated documents (for RAG)
`generate_documents.py` uses the LLM (cheapest configured model) to write markdown into
`data/generated/docs/`, each with YAML front-matter (`doc_id, doc_type, title, machine_model,
component, error_type, failure_id, synthetic: true`):

| doc_type | Count | Content |
|---|---|---|
| `manual` | 4 (one per model) | overview, specs, operating ranges for the 4 sensors, maintenance intervals per component |
| `troubleshooting` | 5 (one per errorID) | symptoms, likely causes, sensor signatures, steps |
| `sop` | 4 (one per component) | replacement procedure, safety, tools, time estimate |
| `incident_report` | configurable `--limit` (default 120) of the ~700+ failures | built from a **real** failure row + the telemetry/errors in the 48h before it: what was observed, root cause (the failed component), action taken, downtime |

Rules: deterministic file names, skip files that already exist (no re-spend), prompt templates
in `backend/app/prompts/docgen_*.md`, operating ranges in manuals must be consistent with the
thresholds computed in §5.4. Commit generated docs to git so others needn't regenerate.

---

## 5. Graph model

### 5.1 Nodes (label → key properties)
| Label | Key | Properties |
|---|---|---|
| `Plant` | `id` | name, location, synthetic |
| `ProductionLine` | `id` | name, synthetic |
| `MachineModel` | `id` (model1..4) | name |
| `Machine` | `id` (e.g. "M017") | machine_id (int), age, status (`running`/`warning`/`alarm`/`down`), health_score (0–100, optional) |
| `Component` | `id` ("M017-comp1") | type (comp1..4), name, last_replaced_at |
| `Sensor` | `id` ("M017-vibration") | type, unit, latest_value, latest_ts, avg_24h, warn_high, crit_high, warn_low, crit_low |
| `ErrorType` | `id` (error1..5) | description |
| `ErrorEvent` | `id` | ts, source (`kaggle` = historical load, `simulator` = live replay) |
| `MaintenanceEvent` | `id` | ts, kind (`replacement`), reason (`scheduled`/`failure`) |
| `Failure` | `id` | ts, description, embedding (vector of description for similar-incident search) |
| `Alarm` | `id` | ts, severity (`warning`/`critical`), sensor_type, value, threshold, status (`open`/`acknowledged`/`resolved`), message, embedding, source (`simulator`) |
| `Technician` | `id` | name, shift, synthetic |
| `Document` | `id` | title, doc_type, path, content_hash, synthetic, machine_model?, component?, error_type? (front-matter metadata, used for retrieval filters; `component` is the type `comp1..4`) |
| `Chunk` | `id` | text, seq, token_count, embedding |

### 5.2 Relationships
```
(:Plant)-[:HAS_LINE]->(:ProductionLine)
(:ProductionLine)-[:HAS_MACHINE {position}]->(:Machine)
(:Machine)-[:FEEDS_INTO]->(:Machine)
(:Machine)-[:IS_MODEL]->(:MachineModel)
(:Machine)-[:HAS_COMPONENT]->(:Component)
(:Machine)-[:HAS_SENSOR]->(:Sensor)
(:ErrorEvent)-[:OCCURRED_ON]->(:Machine)
(:ErrorEvent)-[:OF_TYPE]->(:ErrorType)
(:MaintenanceEvent)-[:PERFORMED_ON]->(:Machine)
(:MaintenanceEvent)-[:REPLACED]->(:Component)
(:MaintenanceEvent)-[:PERFORMED_BY]->(:Technician)
(:Failure)-[:FAILED]->(:Component)
(:Failure)-[:OCCURRED_ON]->(:Machine)
(:Failure)-[:RESOLVED_BY]->(:MaintenanceEvent)
(:Alarm)-[:RAISED_ON]->(:Machine)
(:Alarm)-[:FROM_SENSOR]->(:Sensor)
(:Alarm)-[:SIMILAR_TO {score}]->(:Failure)        // written by alarm engine, top-3
(:Document)-[:HAS_CHUNK]->(:Chunk)
(:Chunk)-[:NEXT]->(:Chunk)
(:Chunk)-[:ABOUT]->(:MachineModel | :Component | :ErrorType)
// ABOUT→Component only for incident reports (the specific failed Component); type-level docs (SOPs)
// are filtered through Document.component instead of fanning out to 100 Component nodes
(:Document)-[:DESCRIBES]->(:Failure)              // incident reports
```

### 5.3 Constraints & indexes (`backend/app/graph/schema.cypher`, applied by `init_schema`)
```cypher
CREATE CONSTRAINT plant_id IF NOT EXISTS FOR (n:Plant) REQUIRE n.id IS UNIQUE;
// ... same for every label in 5.1 ...
CREATE INDEX machine_status IF NOT EXISTS FOR (n:Machine) ON (n.status);
CREATE INDEX alarm_status IF NOT EXISTS FOR (n:Alarm) ON (n.status);
CREATE INDEX failure_ts IF NOT EXISTS FOR (n:Failure) ON (n.ts);

CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS FOR (c:Chunk) ON c.embedding
  OPTIONS { indexConfig: { `vector.dimensions`: 1536, `vector.similarity_function`: 'cosine' } };
CREATE VECTOR INDEX failure_embedding IF NOT EXISTS FOR (f:Failure) ON f.embedding
  OPTIONS { indexConfig: { `vector.dimensions`: 1536, `vector.similarity_function`: 'cosine' } };
CREATE FULLTEXT INDEX chunk_fulltext IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text];
```
Dimensions must match `OPENAI_EMBEDDING_DIMENSIONS` — read from settings and template it in.
Wait for indexes to be ONLINE before ingestion (`SHOW INDEXES`).

### 5.4 Sensor thresholds
Computed once from the full telemetry CSV per sensor type (optionally per model):
`warn_high = μ + 2.5σ`, `crit_high = μ + 3.5σ` (and lows symmetric for volt/pressure).
Store on each `Sensor` node and in `data/generated/thresholds.json`.

### 5.5 Telemetry storage rule
Raw hourly telemetry is **not** loaded into Neo4j. The simulator replays it; the consumer updates
`Sensor.latest_value/latest_ts` and a rolling `avg_24h`. Last 200 readings per sensor are kept in
an in-memory ring buffer in the backend for charts (`GET /api/machines/{id}/telemetry`).

### 5.6 Schema string for the LLM
`backend/app/graph/schema_for_llm.md` — hand-written, compact description of labels, properties
(with example values and allowed enums), relationships and directions. This is what
Text2Cypher gets — not an auto-dump. Keep it in sync with 5.1/5.2 (a test checks every label and
relationship type in the DB appears in this file).

---

## 6. Ingestion pipeline (`backend/app/ingestion/`)

| Module | Does |
|---|---|
| `init_schema.py` | applies `schema.cypher`, waits for indexes ONLINE |
| `build_topology.py` | plants, lines, machines, models, components, sensors, technicians, FEEDS_INTO |
| `load_events.py` | errors, maintenance, failures (+ RESOLVED_BY linking failure → maint same machine/comp/ts); every node gets `source:'kaggle'` |
| `compute_thresholds.py` | §5.4 |
| `generate_documents.py` | §4.3 (CLI flags `--limit`, `--types`, `--dry-run`) |
| `embed_documents.py` | chunk (≈500 tokens, 60 overlap, split on headings first), embed in batches, MERGE Document/Chunk, ABOUT links from front-matter, NEXT links; skip unchanged docs by `content_hash` |
| `embed_failures.py` | build a short text description per Failure (machine model, component, preceding errors, sensor deviations in prior 48h) and embed |
| `run_all.py` | runs everything in order; idempotent; prints node/relationship counts at end |
| `reset_live_state.py` | resets the twin for a clean demo: deletes all `Alarm` nodes and `ErrorEvent {source:'simulator'}`, sets every `Machine.status = 'running'`, clears `Sensor.latest_value/latest_ts/avg_24h`. Never touches structure, Kaggle history (`source:'kaggle'`), documents or embeddings. Also exposed as `POST /api/sim/reset` (only allowed while the simulator is stopped). |

All writes use `UNWIND $rows AS row MERGE ...` in batches of 1,000.
Expected size: well under 50k nodes — runs on AuraDB Free too, but local Docker is the default.

---

## 7. Real-time streaming (`backend/app/streaming/`)

### 7.1 Simulator (`simulator.py`, separate process)
- Replays `PdM_telemetry.csv` in timestamp order, mapping 2015 time → "now".
- `--speed` = simulated hours per real second (default 0.5 → one reading per machine every 2 s).
- Publishes JSON to `factory/{plant}/{line}/{machine_id}/telemetry`:
  `{"machine_id":"M017","ts":"...","volt":170.2,"rotate":446.1,"pressure":101.3,"vibration":39.8}`
- Listens on `factory/control/sim` for `{"cmd":"start|stop|speed|inject", ...}`.
- `inject`: for `machine_id`, `sensor`, `magnitude` (σ multiples), `duration_s` — adds drift
  to that sensor so the alarm engine fires.
- Also replays `PdM_errors.csv` events at their simulated time to `factory/.../errors`.
- **End of data:** the CSV covers one year (~2 real days at default speed). Default: stop and
  publish `sim_status: finished`. With `--loop`: restart from the first timestamp, continuing
  the simulated clock forward.
- **Stop/pause:** `stop` pauses publishing but keeps the process alive (resume with `start`);
  Ctrl+C exits cleanly (disconnect from broker, publish `sim_status: offline` as MQTT last-will).
- Publishes `factory/control/sim/status` (retained) with `{state: running|paused|finished|offline,
  speed, sim_time}` so the backend/UI always know the simulator state.

### 7.1a What the live pipeline writes to Neo4j
| Write | Effect | Grows? |
|---|---|---|
| Sensor readings | `SET` on the 400 existing `Sensor` nodes | No |
| Machine status | `SET` on `Machine.status` | No |
| Alarms | `CREATE` `Alarm {source:'simulator'}` | Slowly |
| Replayed errors | `CREATE` `ErrorEvent {source:'simulator'}` | Slowly |
Raw readings never become nodes. Stopping the simulator freezes the twin at its last values.

### 7.2 Consumer + alarm engine (inside FastAPI lifespan)
- `aiomqtt` subscriber on `factory/#`.
- Updates Neo4j in **micro-batches every 1 s** (one `UNWIND` write per batch, not per message).
- Alarm rule: value beyond `warn_*`/`crit_*` for **3 consecutive readings** → create `Alarm`,
  set `Machine.status`; clear (status `resolved`) after 5 consecutive normal readings.
- On new alarm: embed alarm message, query `failure_embedding` top-3 → `SIMILAR_TO` rels,
  broadcast `alarm_raised` on `/ws/events`, and push a proactive chat message (see §8.6).
- Broadcast `sensor_update` batches to `/ws/events` at most 1×/s.

---

## 8. Chatbot design (`backend/app/chat/`, `backend/app/retrieval/`)

### 8.1 Orchestrator = agent loop with OpenAI tool calling
The LLM decides the route by choosing tools (max **4 tool rounds** per question). Tools:

| Tool | Implementation | Use when |
|---|---|---|
| `query_twin_data(question)` | Text2Cypher (§8.2) | facts, counts, live state, relationships, history in the graph |
| `search_knowledge_base(query, doc_types?, machine_model?, component?)` | vector/hybrid RAG (§8.3) | how-to, procedures, causes, specs |
| `get_machine_context(machine_id)` | **fixed, hand-written Cypher** (not LLM-generated) | any question about a specific machine: sensors latest values vs thresholds, open alarms, components + last replaced, last 5 maintenance, upstream/downstream 2 hops |
| `find_similar_incidents(description, machine_model?)` | vector search on `failure_embedding` + graph context (machine, component, resolution, linked incident report) | diagnosis, "seen this before?" |

The router decision is implicit in tool choice; record it as `route ∈ {cypher, rag, hybrid,
direct}` (hybrid = more than one tool family used; direct = no tool, e.g. greetings) for the UI
and evaluation. Use the OpenAI Python SDK's current recommended API for tool calling (check
installed SDK docs). System prompt: `prompts/orchestrator_system.md`.

### 8.2 Text2Cypher (`retrieval/text2cypher.py`)
- Use `neo4j_graphrag` `Text2CypherRetriever` (or an equivalent thin wrapper) with
  `schema_for_llm.md` and **≥ 25 few-shot examples** in `retrieval/cypher_examples.yaml`
  covering: filters, aggregation, time windows, FEEDS_INTO paths (`*1..3`), alarms, maintenance
  history, technicians, top-N.
- **`cypher_guard.py` (mandatory):**
  1. reject if it contains write/admin clauses: `CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD CSV|CALL\s+(dbms|db\.(create|drop))|apoc\.(create|merge|refactor|periodic)` (case-insensitive, outside string literals)
  2. append `LIMIT 50` if no LIMIT on the final RETURN
  3. `EXPLAIN` it to validate syntax
  4. execute in a **READ transaction** (`session.execute_read` / routing READ) with a 10 s timeout
  Note: Community Edition has no role-based access control, so the READ transaction + guard is
  the protection layer.
- On error: one retry, feeding the error message back to the LLM. On empty result: return
  "no rows" explicitly (the orchestrator may then try RAG).
- Return `{cypher, rows (≤ 50), row_count, truncated, error?}`.

### 8.3 Vector RAG & GraphRAG (`retrieval/vector_rag.py`, `retrieval/graphrag.py`)
- Embedder: `neo4j_graphrag` `OpenAIEmbeddings` with `OPENAI_EMBEDDING_MODEL`.
- `search_knowledge_base` → `HybridCypherRetriever` (vector `chunk_embedding` + fulltext
  `chunk_fulltext`), top_k=5, with a `retrieval_query` that also returns: document title/type,
  neighbouring chunks via `NEXT` (±1), and `ABOUT` targets. Optional metadata filters.
- Drop chunks below a similarity threshold (configurable, start 0.75); if nothing remains,
  return "no relevant documents" so the answer says "I don't know" rather than hallucinating.
- `find_similar_incidents` → `VectorCypherRetriever` on `failure_embedding` with traversal to
  Machine, Component, MaintenanceEvent (resolution) and the incident report Document.

### 8.4 Answer generation
- Final LLM call writes the answer **only from tool results**; cite sources as `[1]`, `[2]`
  mapping to documents/Cypher results. Prompt: `prompts/answer_system.md`.
- Answers are concise, use machine IDs and units, include a short markdown table when listing
  > 3 items, and end with a suggested follow-up question when useful.
- Stream tokens to the client.

### 8.5 Conversation memory
In-memory per `session_id`: last 8 turns + last referenced entities (machine IDs, line IDs) so
follow-ups like *"and what's downstream of it?"* resolve. Include a compact history in the
orchestrator prompt.

### 8.6 Proactive messages
When an alarm is raised, push to all connected chat sessions an assistant message (generated
with the cheap model, from the alarm + top-1 similar incident), e.g.
*"⚠️ M017 vibration 3.1σ above normal. Similar to failure F-0342 (spindle bearing) — ask me
'diagnose M017' for details."* Include a quick-action button payload.

### 8.7 Observability & cost
Per request log: route, tools, cypher, retrieval scores, latency per step, prompt/completion
tokens per call, estimated cost (prices from settings). Return this as `trace` to the UI.
Keep system prompts + schema + examples as a **stable prefix** to benefit from prompt caching.
Optional: LRU cache for identical (question, session-independent) queries.

---

## 9. Backend API (`backend/app/api/`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Neo4j + MQTT connectivity |
| GET | `/api/plants` | plants with lines and machine counts/status summary |
| GET | `/api/machines?line_id=&status=` | machine list with status + latest sensor values |
| GET | `/api/machines/{id}` | detail: model, line, sensors(+thresholds), components, open alarms, last 10 maintenance, upstream/downstream |
| GET | `/api/machines/{id}/telemetry` | ring-buffer series per sensor |
| GET | `/api/graph/neighborhood?node_id=&depth=1..2` | `{nodes, edges}` for Cytoscape |
| GET | `/api/alarms?status=open` | alarms |
| POST | `/api/alarms/{id}/ack` | acknowledge |
| GET | `/api/sim/status` · POST `/api/sim/{start,stop}` · POST `/api/sim/speed` · POST `/api/sim/inject` | simulator control (publishes to control topic) |
| POST | `/api/sim/reset` | runs `reset_live_state` (409 if simulator is running) |
| POST | `/api/chat` | non-streaming fallback: `{session_id, message}` → `{answer, trace}` |
| WS | `/ws/chat` | streaming chat (protocol below) |
| WS | `/ws/events` | `sensor_update`, `alarm_raised` (also on escalation, `escalated:true`), `alarm_resolved`, `alarm_acknowledged`, `machine_status`, `sim_status`, `live_state_reset`; a new client first receives the current `sim_status` |

**`/ws/chat` protocol** (JSON messages, `type` field):
- client → server: `{type:"user_message", session_id, message}`
- server → client, in order: `route` → `tool_call` (name, args) → `tool_result` (summary,
  cypher?, row_count?, sources?) → `token` (delta)… → `done` (full answer, trace) | `error`
- server → client anytime: `proactive` (message, actions[])

Pydantic models for every request/response live in `app/models/`. CORS allows
`http://localhost:4200`.

---

## 10. Frontend (Angular) — `frontend/`

Create with `npx @angular/cli@latest new frontend --style=scss --routing --ssr=false`, add
Angular Material. Standalone components, signals, `@if/@for` control flow, strict TypeScript.
Dev proxy `proxy.conf.json`: `/api` and `/ws` → `http://localhost:8000` (ws: true).

### 10.1 Structure
```
src/app/
  core/
    models/            typed interfaces mirroring backend pydantic models
    services/          api.service.ts, events-socket.service.ts, chat-socket.service.ts
    state/             factory.store.ts (signals: machines, alarms, simStatus)
  features/
    dashboard/         plant/line overview, machine tiles (status colour, mini sparkline)
    machine-detail/    sensor gauges + ECharts series w/ threshold lines, alarms, maintenance, neighborhood graph
    graph-explorer/    Cytoscape view, click node → details, expand neighbours
    simulator-panel/   start/stop, speed slider, inject anomaly form (machine, sensor, magnitude), reset button (enabled only when stopped), state badge (running/paused/finished/offline)
    chat/              chat-drawer, message-list, message (markdown), trace-panel, suggested-questions
  shared/              status-badge, sensor-gauge, toast/alarm-snackbar
```

### 10.2 Chat UX requirements
- Right-side drawer available on every page (toggle button), persists across navigation.
- Streaming tokens render live; markdown + tables via ngx-markdown.
- **Trace panel** ("How I answered", collapsed by default): route badge, tool calls in order,
  Cypher in a code block with copy button, results preview table (first 10 rows), sources list
  (doc title + type), latency and estimated cost.
- Suggested starter questions (one per route) when the chat is empty.
- Alarm snackbar has **"Ask about this"** → opens chat prefilled with
  *"Diagnose {machine_id}: {sensor} alarm"*.
- Machine IDs in answers become links to machine detail (regex `M\d{3}`).
- Reconnect WebSockets automatically with backoff; show connection status dot.

### 10.3 Look & feel
Clean industrial dashboard: dark theme default, status colours (running green, warning amber,
alarm red, down grey), responsive down to laptop width. Use the `frontend-design` sensibility:
deliberate typography and spacing, not default Material everywhere.

---

## 11. Configuration

`.env.example` (copy to `.env`):
```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=change-me
MQTT_HOST=localhost
MQTT_PORT=1883

OPENAI_API_KEY=sk-...
# Pick current models from https://platform.openai.com/docs/pricing
OPENAI_ORCHESTRATOR_MODEL=   # capable, tool-calling model (mini tier is usually enough)
OPENAI_CYPHER_MODEL=         # best accuracy you can afford; Cypher quality matters most
OPENAI_ANSWER_MODEL=         # mini tier
OPENAI_CHEAP_MODEL=          # nano tier: doc generation, proactive messages
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_EMBEDDING_DIMENSIONS=1536
# USD per 1M tokens, used for cost estimates in traces
PRICE_INPUT_PER_M=
PRICE_OUTPUT_PER_M=

RAG_MIN_SCORE=0.68               # spec suggested 0.75; calibrated on the real corpus, see README
SIM_SPEED=0.5
ENABLE_STREAMING=1           # 0 = don't run the MQTT consumer/alarm engine in the API process
```

`docker-compose.yml`:
```yaml
services:
  neo4j:
    image: neo4j:latest            # Community Edition; pin the exact tag after first run
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_server_memory_heap_max__size: 1G
      NEO4J_server_memory_pagecache_size: 512M
    volumes: ["./.neo4j/data:/data", "./.neo4j/logs:/logs"]
  mosquitto:
    image: eclipse-mosquitto:2
    ports: ["1883:1883"]
    volumes: ["./infra/mosquitto.conf:/mosquitto/config/mosquitto.conf"]
```
`infra/mosquitto.conf`: `listener 1883` + `allow_anonymous true` (local dev only).

---

## 12. Implementation phases & checklist

Work strictly in order. After each phase: run tests/lint, tick the boxes here, summarize, stop.

### Phase 0 — Scaffold
- [x] Repo layout per CLAUDE.md, `.gitignore` (.env, data/raw, .neo4j, node_modules, .venv)
- [x] `docker-compose.yml`, `infra/mosquitto.conf`, `.env.example`
- [x] `backend/` uv project (pyproject with deps, ruff + mypy + pytest config), `app/main.py` with `/api/health`, settings via pydantic-settings, Neo4j driver lifecycle
- [x] `frontend/` Angular app with Material, proxy config, empty shell layout (toolbar, sidenav routes, chat drawer placeholder)
- [x] README with setup steps
**Accept:** `docker compose up -d` healthy; `/api/health` returns neo4j ok + mqtt ok; Angular shell loads at :4200 and shows backend health.

### Phase 1 — Graph & ingestion (structured)
- [x] `schema.cypher` + `init_schema`
- [x] `build_topology`, `load_events`, `compute_thresholds`, `run_all` (without docs)
- [x] `schema_for_llm.md`
- [x] Tests: idempotency (run twice → same counts), constraint presence, FEEDS_INTO has no cycles within a line, every machine has 4 sensors + 4 components
**Accept (done on the real Kaggle data):** counts printed; sample Cypher in Neo4j Browser shows plant → line → machines → sensors; re-run creates no duplicates.

### Phase 2 — Documents & embeddings
- [x] `generate_documents` (with `--dry-run` cost estimate), prompts in `prompts/`
- [x] `embed_documents`, `embed_failures`, vector + fulltext indexes ONLINE
- [x] Test: vector query for "bearing overheating" returns spindle-bearing content in top-3
**Accept (done on real data: 133 docs, 147 chunks, 761 failure embeddings, re-run embeds 0):** ~130 documents, chunks with embeddings, re-run embeds 0 unchanged docs.

### Phase 3 — Retrieval components
- [x] `cypher_guard` with unit tests (write clauses rejected, LIMIT appended, strings with "create" inside literals allowed)
- [x] `text2cypher` with ≥ 25 examples, retry on error
- [x] `vector_rag` (hybrid), `graphrag` similar incidents, `get_machine_context` fixed query
- [x] CLI `python -m app.retrieval.try "<question>"` printing tool output
**Accept (done: guard tested offline; tools verified live and in the 45-question eval):** 10 sample questions per tool produce sensible output; guard tests pass.

### Phase 4 — Chat orchestrator & API
- [x] Orchestrator agent loop with 4 tools, route classification, memory, trace + cost
- [x] `POST /api/chat` and `WS /ws/chat` with the streaming protocol
- [x] Prompts: `orchestrator_system.md`, `answer_system.md`
**Accept (done: demo questions answered live with full trace):** the 3 demo questions (§1 steps 2, 3, 5 — without live alarm) answer correctly via WS with full trace.

### Phase 5 — Real-time
- [x] Simulator with control topic + anomaly injection, `--loop`, pause/resume, retained status topic + last-will
- [x] `reset_live_state` script + `POST /api/sim/reset`; all live-created nodes carry `source:'simulator'`
- [x] Consumer micro-batching, alarm engine, SIMILAR_TO, `/ws/events`, sim REST endpoints
- [x] Proactive chat message on alarm
**Accept:** start sim → sensor values change in Neo4j; inject anomaly on M017 → alarm node within ~10 s, event on `/ws/events`, proactive chat message; stop → values freeze and UI shows "paused"; reset → no alarms, all machines `running`, Kaggle history unchanged (node counts by `source:'kaggle'` identical before/after).

### Phase 6 — Frontend features
- [x] Dashboard with live tiles, machine detail with charts + thresholds, graph explorer, simulator panel
- [x] Chat drawer with streaming, trace panel, suggestions, alarm "Ask about this", machine links
**Accept (verified: 62 component/store tests + full-stack run of demo steps 1-5 against the real data and live OpenAI; NOT yet checked visually in a browser):** full demo story §1 runs end-to-end in the browser.

### Phase 7 — Evaluation & polish
- [x] `eval/questions.yaml`: 45 questions (15 cypher, 15 rag, 15 hybrid) with `expected_route`, and per type: `gold_cypher` (compare result sets), `must_mention` keywords, `expected_sources`
- [x] `eval/run_eval.py` → `eval/report.md`: route accuracy, Cypher execution accuracy, RAG hit@5, answer keyword recall, p50/p95 latency, avg cost/question
- [x] Optional: compare 2 models for `OPENAI_CYPHER_MODEL`; compare vector-only vs hybrid retrieval
- [x] README: architecture diagram, screenshots/GIF, eval table, cost notes, "synthetic data" disclaimer, design decisions (telemetry outside graph, read-only Cypher, fixed vs generated Cypher)
**Accept (done: report generated, see README):** report generated; README complete.

---

## 13. Testing strategy
- **Unit:** cypher_guard, threshold logic, alarm debounce, chunker, topology determinism.
- **Integration (needs Docker Neo4j):** ingestion idempotency, retrievers against seeded DB.
  Mark with `@pytest.mark.integration`.
- **LLM-dependent tests:** marked `@pytest.mark.llm`, skipped unless `RUN_LLM_TESTS=1` (cost control).
- **Frontend:** component tests for chat message rendering and trace panel; services with mocked sockets.

## 14. Example questions (also seed for eval)
**Cypher:** Which machines on line A2 have open alarms? · How many failures per component type in 2015? · Which technician performed the most replacements? · List machines older than 15 years with a critical alarm. · What machines are downstream of M017 within 3 hops? · When was M042's hydraulic pump last replaced?
**RAG:** How do I replace a spindle bearing? · What does error3 usually indicate? · What is the normal pressure range for model2? · What safety steps apply before coolant valve work?
**Hybrid:** Diagnose M017. · M055 shows high vibration — have we seen this before and what fixed it? · Which open alarms are most likely to cause a failure, and what does the manual recommend? · If M030 goes down, which lines are affected and what's the procedure to restore it?
