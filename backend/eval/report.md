# Evaluation report

Generated 2026-10-28 21:16 UTC · 45 questions (0 failed to run) · synthetic data

Models: orchestrator `gpt-6-luna`, cypher `gpt-6-luna`, answer `gpt-6-luna`, embeddings `text-embedding-3-small`. RAG similarity threshold 0.68.

## Summary

| Metric | Value |
|---|---|
| Route accuracy | 100% |
| · cypher questions | 100% |
| · rag questions | 100% |
| · hybrid questions | 100% |
| Cypher execution rate | 100% |
| Cypher result accuracy, strict (identical result set) | 47% |
| Cypher result accuracy, lenient (gold values present) | 100% |
| RAG hit@5 | 94% |
| Expected tools used | 93% |
| Answer keyword recall | 98% |
| Latency p50 / p95 | 7786 ms / 23155 ms |
| Avg cost per question | $0.0000 |
| Total cost | $0.000 |

## Retrieval: vector-only vs hybrid

| Mode | hit@5 | Questions |
|---|---|---|
| vector | 100% | 15 |
| hybrid | 100% | 15 |

## Questions that need attention

| id | question | route (expected) | issues |
|---|---|---|---|
| c03 | What is the highest machine age in the factory? | cypher (cypher) | cypher result differs |
| c04 | How many failures has M042 had in total? | cypher (cypher) | cypher result differs |
| c05 | Which component type has failed most often overall? | cypher (cypher) | cypher result differs |
| c07 | Which technicians work the night shift? | cypher (cypher) | cypher result differs |
| c12 | Which machines on line A1 are model4? | cypher (cypher) | cypher result differs |
| c13 | How many production lines does the Leipzig plant have? | cypher (cypher) | cypher result differs |
| c14 | What is the average age of model1 machines? | cypher (cypher) | cypher result differs; keyword recall 0% |
| c15 | Which technician replaced the most spindle bearings? | cypher (cypher) | cypher result differs |
| r11 | What are the common issues on model3 machines? | rag (rag) | expected source not in top 5 (got troubleshooting-error3, troubleshooting-error5, troubleshooting-error2, troubleshooting-error4, incident-F-0143) |
| h05 | What is downstream of M012 and how do I replace its spindle bearing? | hybrid (hybrid) | tools used: query_twin_data, search_knowledge_base |
