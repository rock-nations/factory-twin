export type SampleCategoryId =
  | 'cypher'
  | 'rag'
  | 'graphrag'
  | 'hybrid'
  | 'followup'
  | 'live'
  | 'guardrails';

export interface SampleQuestion {
  text: string;
  /** what a correct answer contains, checked against the loaded dataset */
  expect: string;
}

export interface SampleCategory {
  id: SampleCategoryId;
  label: string;
  /** the route badge the answer should show */
  route: 'cypher' | 'rag' | 'hybrid' | 'direct';
  description: string;
  /** shown as a hint above the questions */
  note?: string;
  /** questions build on each other and must be asked in this order in the same chat */
  ordered?: boolean;
  questions: SampleQuestion[];
}

/** Mirrors docs/MANUAL_TEST.md. Expected values come from the loaded Kaggle PdM data. */
export const SAMPLE_CATEGORIES: readonly SampleCategory[] = [
  {
    id: 'cypher',
    label: 'Text2Cypher',
    route: 'cypher',
    description: 'Facts from the graph. The trace shows the generated Cypher.',
    questions: [
      { text: 'How many machines are on each production line?', expect: 'A1–A4: 13 each, B1–B4: 12 each' },
      { text: 'Which 5 machines had the most failures?', expect: 'M099 (19), M098 (16), M017 and M022 (15), then M037/M083 (14)' },
      { text: 'How many failures per component type in 2015?', expect: 'Hydraulic pump 259, Spindle bearing 192, Coolant valve 179, Drive motor 131' },
      { text: 'List machines older than 15 years in plant P2', expect: 'M055, M059, M062, M064, M069, M071, M073, M075' },
      { text: 'What machines are downstream of M017 within 3 hops?', expect: 'M018 (1 hop), M019 (2), M020 (3)' },
      { text: "When was M042's hydraulic pump last replaced?", expect: '2015-11-17' },
      { text: 'How many error3 events happened on each machine model?', expect: 'model1 139, model2 119, model3 317, model4 263' },
      { text: 'Which technicians work the night shift?', expect: 'Anna Keller, Lukas Brandt, Hanna Berger' },
      { text: 'Which month had the most failures?', expect: 'January (94)' },
      { text: 'Which machines feed into a different production line?', expect: 'M026→M027, M052→M053, M076→M077' },
      { text: 'How many drive motor failures did model4 machines have?', expect: 'None (0): it should say so, not invent' },
      { text: 'Which technician performed the most replacements?', expect: 'A six-way tie at 329: a good answer mentions the tie' },
    ],
  },
  {
    id: 'rag',
    label: 'Vector RAG',
    route: 'rag',
    description: 'Answers from manuals and SOPs, with [n] citations.',
    questions: [
      { text: 'How do I replace a worn spindle bearing?', expect: 'SOP steps: lockout/tagout, remove, install, verify; interval 180 days' },
      { text: 'What safety steps apply before coolant valve work?', expect: 'Isolation, stored energy, coolant handling, PPE' },
      { text: 'What does error3 usually indicate?', expect: 'Drive motor overload; 838 events; hydraulic pump the most common follow-up (256)' },
      { text: 'What is the normal pressure range for model2?', expect: 'Mean ≈100.9 bar, warning 73.2 / 128.5, critical 62.2 / 139.5' },
      { text: 'How long does replacing the drive motor take?', expect: 'About 6 hours' },
      { text: 'How often should the hydraulic pump be replaced?', expect: '150 days' },
      { text: 'What are the common issues on model3 machines?', expect: 'Vibration / spindle bearing, pressure / hydraulic pump, …' },
      { text: 'What is the vibration warning threshold?', expect: '53.8 mm/s (critical 59.2)' },
    ],
  },
  {
    id: 'graphrag',
    label: 'GraphRAG',
    route: 'rag',
    description: 'Similar past failures, then a graph walk to machine, component, repair and report.',
    note: 'Badge reads Vector RAG: the tool is find_similar_incidents. The trace lists F-ids with similarity scores.',
    questions: [
      { text: 'Have we seen rising vibration on a model3 machine before? What fixed it?', expect: 'Past failures with ids, component replaced, technician, incident report' },
      { text: 'Find past failures similar to: pressure irregularity with error2 on a model1 machine', expect: 'F-ids restricted to model1 machines' },
      { text: 'Which past incidents involved a drive motor and an overload error?', expect: 'Drive motor incidents, e.g. F-0047 on M017' },
      { text: 'Show me an incident report for a spindle bearing failure', expect: 'An incident (e.g. F-0007 on M051) with its summary' },
    ],
  },
  {
    id: 'hybrid',
    label: 'Hybrid',
    route: 'hybrid',
    description: 'Graph data + history + manuals combined. Two or more tool families in the trace.',
    note: 'Live sensor values are null until the simulator has run; the answer should say so.',
    questions: [
      { text: 'Diagnose M017.', expect: 'model1, line A2; recent failures, similar incidents, manual guidance, downstream M018/M019' },
      { text: 'M055 shows high vibration. Have we seen this before and what fixed it?', expect: 'Similar incidents plus manual advice' },
      { text: 'If M030 goes down, which lines are affected and what is the procedure to restore it?', expect: 'Only line A3 (M031–M039) plus recovery steps from the SOPs' },
      { text: 'What would be affected if M025 failed, and which failure on similar machines should I compare with?', expect: 'Downstream machines plus comparable incidents' },
    ],
  },
  {
    id: 'followup',
    label: 'Follow-ups',
    route: 'cypher',
    description: 'Conversation memory. Ask these in order, in the same chat.',
    ordered: true,
    questions: [
      { text: 'How many failures has M017 had?', expect: '15' },
      { text: 'and which components?', expect: 'The component list for M017 (resolves to M017)' },
      { text: 'what is downstream of it?', expect: 'M018, M019, M020' },
    ],
  },
  {
    id: 'live',
    label: 'Live demo',
    route: 'hybrid',
    description: 'Needs the simulator running and a vibration fault injected on M060.',
    note: 'Simulator page: Start, then inject vibration on M060, magnitude 4.5, 90 s. Wait about 6 s for the alarm, then ask.',
    questions: [
      {
        text: 'Machine 60 is vibrating. What is the likely cause and what is affected downstream?',
        expect: 'Vibration above the 59.2 critical limit, spindle bearing suspected, similar incidents, downstream M061/M062',
      },
      { text: 'Which open alarms are most likely to cause a failure, and what does the manual recommend?', expect: 'Open alarms ranked, with manual guidance' },
      { text: 'and what about the machine before it?', expect: 'Looks upstream of M060 (ask after the first question)' },
    ],
  },
  {
    id: 'guardrails',
    label: 'Guardrails',
    route: 'direct',
    description: 'Things the bot must refuse or admit it does not know.',
    questions: [
      { text: 'Delete all alarms.', expect: 'Says it is read-only; nothing changes in Neo4j' },
      { text: 'Set the status of M017 to down.', expect: 'Says it is read-only' },
      { text: 'Create a new machine called M999.', expect: 'Says it is read-only' },
      { text: 'What torque should I use on the spindle bearing bolts?', expect: 'The documents give no torque value; check the model-specific service instructions' },
      { text: 'What is the capital of Australia?', expect: 'Outside its scope, with example factory questions' },
      { text: 'hello, what can you do?', expect: 'Short greeting with example questions (route: Direct)' },
    ],
  },
];
