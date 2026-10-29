# Manual test sheet

How to try the copilot by hand and what a correct answer looks like. Expected values come from the
loaded graph (100 machines, 761 failures, 133 documents), so you can check the bot against them.

## 0. Start everything

```bash
docker compose up -d                                   # Neo4j + Mosquitto
cd backend
uv run uvicorn app.main:app --reload                   # terminal 1: API  -> http://localhost:8000
uv run python -m app.streaming.simulator --paused      # terminal 2: simulator, waits for "Start"
cd ../frontend && npm start                            # terminal 3: UI   -> http://localhost:4200
```

- UI: <http://localhost:4200>, open the chat with the speech-bubble icon (top right).
- Neo4j Browser: <http://localhost:7474> (user `neo4j`, password from `.env`) to check any answer.
- The chat shows these questions as clickable samples, grouped by category (tabs), each with its expected
  answer. Click a question to send it, or the pencil to edit it first. After the first message, the
  **Samples** button in the chat header brings the list back. The list lives in
  `frontend/src/app/core/models/sample-questions.ts`.
- After every question open **How I answered** under the reply: it shows the route badge, tools, the
  Cypher, sources, latency, tokens and estimated cost.
- Each answer takes roughly 5 to 20 seconds (three to four sequential model calls).

Before the live sections, press **Start** on the *Simulator* page. The real data already raises its own
alarms in the first simulated day (M016, M017, M045, M073, M095 ...), so alarms appear without injecting
anything. **Reset live state** (simulator stopped) clears them.

## 1. Text2Cypher (graph facts)

Expected route badge: **Text2Cypher**. Tool: `query_twin_data`. The trace must show a Cypher block.

| # | Ask | Expected answer | Check |
|---|---|---|---|
| 1 | How many machines are on each production line? | A1-A4: 13 each, B1-B4: 12 each | counts |
| 2 | Which 5 machines had the most failures? | M099 (19), M098 (16), M017 and M022 (15), then M037/M083 (14) | ranking, ties |
| 3 | How many failures per component type in 2015? | Hydraulic pump 259, Spindle bearing 192, Coolant valve 179, Drive motor 131 | names, not `comp2` |
| 4 | List machines older than 15 years in plant P2 | M055, M059, M062, M064, M069, M071, M073, M075 (17-20 y) | table for more than 3 rows |
| 5 | What machines are downstream of M017 within 3 hops? | M018 (1 hop), M019 (2), M020 (3) | `FEEDS_INTO*1..3` in the Cypher |
| 6 | When was M042's hydraulic pump last replaced? | 2015-11-17 | date |
| 7 | How many error3 events happened on each machine model? | model1 139, model2 119, model3 317, model4 263 | grouping by model |
| 8 | Which technicians work the night shift? | Anna Keller, Lukas Brandt, Hanna Berger | |
| 9 | Which month had the most failures? | January (94) | |
| 10 | Which machines feed into a different production line? | M026 to M027 (A2 to A3), M052 to M053 (A4 to B1), M076 to M077 (B2 to B3) | |
| 11 | How many drive motor failures did model4 machines have? | 0 (a quirk of the dataset) | must say "none / 0", not invent |
| 12 | Which technician performed the most replacements? | **Six-way tie** at 329 (the other four have 328) | a good answer mentions the tie, not one name |

Follow-up memory (ask in the same chat, in order):
- "How many failures has M017 had?" then "and which components?" then "what is downstream of it?"
  Expected: 15 failures, then the component list, then M018/M019/M020. The bot should resolve "it" to M017.

Read-only checks. The bot must say it cannot do these, and nothing in the database may change (verify in
Neo4j Browser). It may still run a harmless read query behind the scenes; that is fine.
- "Delete all alarms."
- "Set the status of M017 to down."
- "Create a new machine called M999."

The write-protection itself is enforced below the model (a Cypher guard plus a read-only transaction),
so even a misbehaving model cannot modify the graph. That is covered by automated tests.

## 2. Vector RAG (documents)

Expected route badge: **Vector RAG**. Tool: `search_knowledge_base`. Answers must cite `[n]` and the
sources list in the trace must show document titles.

| # | Ask | Expected |
|---|---|---|
| 1 | How do I replace a worn spindle bearing? | Steps from the spindle-bearing SOP: lockout/tagout, remove, install, verify. Notes the interval (180 days) |
| 2 | What safety steps apply before coolant valve work? | From the coolant-valve SOP (isolation, stored energy, coolant handling, PPE) |
| 3 | What does error3 usually indicate? | Drive motor overload, 838 events in history, small sensor signature, hydraulic pump most common follow-up (256) |
| 4 | What is the normal pressure range for model2? | Mean about 100.9 bar, warning 73.2 (low) and 128.5 (high), critical 62.2 and 139.5 |
| 5 | How long does replacing the drive motor take? | About 6 hours |
| 6 | How often should the hydraulic pump be replaced? | 150 days |
| 7 | What are the common issues on model3 machines? | From the model3 manual: vibration/spindle bearing, pressure/hydraulic pump, and so on |
| 8 | What is the vibration warning threshold? | 53.8 mm/s (critical 59.2) |

"I don't know" and scope checks (the bot must not guess or use general knowledge):
- "What torque should I use on the spindle bearing bolts?" Expected: the SOP gives no torque value, so
  consult the model-specific service instructions.
- "What is the capital of Australia?" and "How do I bake sourdough bread?" Expected: outside what it can
  help with, plus example factory questions.
- "hello, what can you do?" Expected: a short greeting with example questions (route badge: Direct).

## 3. GraphRAG (similar past incidents)

Expected tool: `find_similar_incidents`. The route badge reads **Vector RAG**, because this tool alone is
in the retrieval family; it is the graph-enhanced variant. It does a vector search over the 761 past failures and then walks
the graph to the machine, component, repair and incident report. The tool result in the trace shows
failure ids (`F-xxxx`) with similarity scores.

| # | Ask | Look for |
|---|---|---|
| 1 | Have we seen rising vibration on a model3 machine before? What fixed it? | Past failures with ids, the component replaced, technician, and the incident report title |
| 2 | Find past failures similar to: pressure irregularity with error2 on a model1 machine | A list of F-ids restricted to model1 machines |
| 3 | Which past incidents involved a drive motor and an overload error? | Drive motor incidents (e.g. F-0047 on M017) |
| 4 | Show me an incident report for a spindle bearing failure | An incident (e.g. F-0007 on M051) with its summary |

Compare with the incident report in the sources list; the machine, component and technician in the
answer must match it.

## 4. Hybrid (live data + history + documents + graph)

Expected route badge: **Hybrid GraphRAG**. Expect two or more tool families in the trace.

Static (works without the simulator, but live readings are then `null` and the bot should say so):

| # | Ask | Expected |
|---|---|---|
| 1 | Diagnose M017. | Machine M017 (model1, line A2, age 14), recent failures, similar incidents, manual guidance, downstream M018/M019 |
| 2 | M055 shows high vibration. Have we seen this before and what fixed it? | Similar incidents plus manual advice |
| 3 | If M030 goes down, which lines are affected and what is the procedure to restore it? | Only line A3 (M031 to M039) is downstream; recovery steps from the SOPs |
| 4 | What would be affected if M025 failed, and which failure on similar machines should I compare with? | Downstream machines, plus comparable incidents |

Live demo (the full story):
1. *Simulator* page: **Start**. Dashboard tiles start moving; alarms from the real data appear.
2. *Simulator*: inject **vibration**, machine **M060**, magnitude **4.5**, duration **90 s**.
3. Within about 6 seconds: an alarm toast with **Ask about this**, the tile turns red, and the chat shows
   a proactive warning mentioning a similar failure.
4. Click **Diagnose M060** or ask: "Machine 60 is vibrating - what is the likely cause and what is
   affected downstream?"
   Expected: live vibration above the 59.2 mm/s critical limit, spindle bearing as a suspect (not
   certain), similar incidents, manual guidance, downstream **M061** and **M062**.
5. Ask: "Which open alarms are most likely to cause a failure, and what does the manual recommend?"
6. Memory: after step 4 ask "and what about the machine before it?" It should look upstream of M060.
7. Stop the simulator, then **Reset live state** (two clicks). Alarms disappear and all tiles go green.

## 5. UI checklist

- Dashboard: 2 plants, 8 lines, 100 tiles; status counts in the header; tiles open the machine page.
- Machine page `/machines/M017`: four gauges with warn/critical bands, charts with threshold lines (fill
  in once the simulator runs), components, maintenance, upstream/downstream pills, neighbourhood graph
  (double-click a node to expand).
- Graph explorer: load `M017`, `A2`, `model3`; click a node for its properties; try depth 2.
- Chat: streaming text, markdown tables, machine ids such as M017 are clickable links, starter
  questions when empty, route badge, collapsible **How I answered**, copy button on the Cypher.
- Simulator: Reset is disabled while running; Inject is disabled while stopped; state badge changes.
- Stop the API for a moment: the top bar goes red and the chat dot changes; restart and it reconnects.

## 6. Command-line shortcuts (no UI)

```bash
cd backend
uv run python -m app.retrieval.try "Which machines feed into M050?"            # Text2Cypher
uv run python -m app.retrieval.try --tool rag "How do I replace a spindle bearing?"
uv run python -m app.retrieval.try --tool context M017                         # fixed context query
uv run python -m app.retrieval.try --tool similar "rising vibration" --model model3
curl -s -X POST localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"session_id":"t1","message":"Diagnose M017"}' | python3 -m json.tool
```

## Known quirks (not bugs)

- Live sensor values are `null` until the simulator has run; answers should say so.
- The real data creates alarms by itself (M016/M017 really fail on 2 January 2015).
- Three technicians tie for "most replacements" because assignment is round-robin.
- Model4 machines have no drive-motor failures in this dataset.
- All documents are LLM-written from the real data and contain no vendor specifics (no torque values,
  part numbers); the bot should say that rather than invent them.
- Answers are non-deterministic; wording, and sometimes the extra columns in the Cypher, vary run to run.
