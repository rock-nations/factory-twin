# Factory digital twin — graph schema

Neo4j property graph. All `ts` properties are Neo4j `DateTime` values (compare with
`datetime('2015-06-01T00:00:00')`, extract with `.year`, `.month`; durations with
`duration.between(a, b).hours`). IDs are strings.

## Nodes

- `(:Plant {id, name, location})` — id `P1` (Munich Assembly), `P2` (Leipzig Machining)
- `(:ProductionLine {id, name})` — id `A1`..`A4` (plant P1), `B1`..`B4` (plant P2); name `Line A2`
- `(:MachineModel {id, name})` — id `model1`..`model4`
- `(:Machine {id, machine_id, age, status, health_score})` — id like `M017`; `machine_id` integer 17;
  `age` in years (int); `status` one of `running`, `warning`, `alarm`, `down`
- `(:Component {id, type, name, last_replaced_at})` — id `M017-comp1`; `type` `comp1`..`comp4`;
  name: comp1 `Spindle bearing`, comp2 `Hydraulic pump`, comp3 `Drive motor`, comp4 `Coolant valve`
- `(:Sensor {id, type, unit, latest_value, latest_ts, avg_24h, warn_high, crit_high, warn_low, crit_low})`
  — id `M017-vibration`; `type` one of `volt` (V), `rotate` (rpm), `pressure` (bar), `vibration` (mm/s).
  `latest_value`/`avg_24h` are null until the simulator runs. `*_low` are only set for volt and pressure.
- `(:ErrorType {id, description})` — id `error1`..`error5` (non-fatal error codes)
- `(:ErrorEvent {id, ts, source})` — `source` is `kaggle` (history) or `simulator` (live replay)
- `(:MaintenanceEvent {id, ts, kind, reason, source})` — `kind` is `replacement`;
  `reason` is `scheduled` or `failure`
- `(:Failure {id, ts, description, source})` — id like `F-0342`; a machine breakdown caused by one component
- `(:Alarm {id, ts, severity, sensor_type, value, threshold, status, message, source})` —
  `severity` `warning` | `critical`; `status` `open` | `acknowledged` | `resolved`; `source` is `simulator`
- `(:Technician {id, name, shift})` — `shift` is `day`, `evening` or `night`
- `(:Document {id, title, doc_type, path, machine_model, component, error_type})` — `doc_type` one of `manual`,
  `troubleshooting`, `sop`, `incident_report`; the last three are optional metadata (`component` is `comp1`..`comp4`)
- `(:Chunk {id, text, seq, token_count})` — a passage of a Document (used for text search, not for Cypher answers)

## Relationships

- `(:Plant)-[:HAS_LINE]->(:ProductionLine)`
- `(:ProductionLine)-[:HAS_MACHINE {position}]->(:Machine)` — `position` is the 1-based order in the line
- `(:Machine)-[:FEEDS_INTO]->(:Machine)` — material flow; the source is upstream, the target downstream.
  Includes a few cross-line links. Use variable-length paths for "downstream/upstream within N hops":
  `(m)-[:FEEDS_INTO*1..3]->(d)`
- `(:Machine)-[:IS_MODEL]->(:MachineModel)`
- `(:Machine)-[:HAS_COMPONENT]->(:Component)`
- `(:Machine)-[:HAS_SENSOR]->(:Sensor)`
- `(:ErrorEvent)-[:OCCURRED_ON]->(:Machine)`
- `(:ErrorEvent)-[:OF_TYPE]->(:ErrorType)`
- `(:MaintenanceEvent)-[:PERFORMED_ON]->(:Machine)`
- `(:MaintenanceEvent)-[:REPLACED]->(:Component)`
- `(:MaintenanceEvent)-[:PERFORMED_BY]->(:Technician)`
- `(:Failure)-[:FAILED]->(:Component)`
- `(:Failure)-[:OCCURRED_ON]->(:Machine)`
- `(:Failure)-[:RESOLVED_BY]->(:MaintenanceEvent)`
- `(:Alarm)-[:RAISED_ON]->(:Machine)`
- `(:Alarm)-[:FROM_SENSOR]->(:Sensor)`
- `(:Alarm)-[:SIMILAR_TO {score}]->(:Failure)` — the most similar past failures (top 3)
- `(:Document)-[:HAS_CHUNK]->(:Chunk)`, `(:Chunk)-[:NEXT]->(:Chunk)`
- `(:Chunk)-[:ABOUT]->(:MachineModel | :Component | :ErrorType)`
- `(:Document)-[:DESCRIBES]->(:Failure)` — incident reports

## Notes

- A machine's line: `(:ProductionLine)-[:HAS_MACHINE]->(m)`; its plant via `(:Plant)-[:HAS_LINE]->(:ProductionLine)`.
- "Open alarms" means `Alarm.status = 'open'`.
- A machine's model name is on `MachineModel.id` (`model3`), reached via `IS_MODEL`.
- Component names are on `Component.name`; to filter by kind use `Component.type` (`comp1`..`comp4`) or the name.
- Raw sensor readings are NOT stored; only the latest value and a 24h average on `Sensor`.
