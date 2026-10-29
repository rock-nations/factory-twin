export type Route = 'cypher' | 'rag' | 'hybrid' | 'direct';

export interface SourceRef {
  ref: number;
  kind: 'cypher' | 'document' | 'incident' | 'machine';
  title: string;
  doc_id: string | null;
  doc_type: string | null;
}

export interface ToolTrace {
  name: string;
  arguments: Record<string, unknown>;
  summary: string;
  cypher: string | null;
  row_count: number | null;
  truncated: boolean;
  preview_rows: Record<string, unknown>[];
  sources: SourceRef[];
  retrieval_scores: number[];
  latency_ms: number;
  error: string | null;
}

export interface LlmCall {
  step: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cost_usd: number;
  latency_ms: number;
}

export interface Trace {
  route: Route;
  tools: ToolTrace[];
  llm_calls: LlmCall[];
  rounds: number;
  total_latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  step_latency_ms: Record<string, number>;
}

export interface ProactiveAction {
  label: string;
  message: string;
}

/** Server -> client messages on /ws/chat. */
export type ChatServerMessage =
  | { type: 'route'; route: Route }
  | { type: 'tool_call'; name: string; args: Record<string, unknown> }
  | {
      type: 'tool_result';
      name: string;
      summary: string;
      cypher?: string | null;
      row_count?: number | null;
      sources: SourceRef[];
    }
  | { type: 'token'; delta: string }
  | { type: 'done'; answer: string; trace: Trace }
  | { type: 'error'; message: string }
  | { type: 'proactive'; message: string; actions: ProactiveAction[]; machine_id?: string };

export type ChatRole = 'user' | 'assistant' | 'proactive' | 'error';

export interface LiveStep {
  name: string;
  args: Record<string, unknown>;
  summary?: string;
  done: boolean;
}

export interface ChatMessage {
  id: number;
  role: ChatRole;
  text: string;
  streaming?: boolean;
  route?: Route;
  steps?: LiveStep[];
  trace?: Trace;
  actions?: ProactiveAction[];
}
