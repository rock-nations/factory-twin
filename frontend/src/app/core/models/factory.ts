export type MachineStatus = 'running' | 'warning' | 'alarm' | 'down';
export type SensorState = 'normal' | 'warning' | 'critical' | 'no_data';
export type SensorName = 'volt' | 'rotate' | 'pressure' | 'vibration';

export const SENSOR_NAMES: readonly SensorName[] = ['volt', 'rotate', 'pressure', 'vibration'];

export interface StatusCounts {
  running: number;
  warning: number;
  alarm: number;
  down: number;
}

export interface LineSummary {
  id: string;
  name: string;
  machine_count: number;
  status_counts: StatusCounts;
}

export interface PlantSummary {
  id: string;
  name: string;
  location: string | null;
  machine_count: number;
  status_counts: StatusCounts;
  lines: LineSummary[];
}

export interface SensorReading {
  sensor: SensorName;
  unit: string;
  value: number | null;
  as_of: string | null;
  avg_24h: number | null;
  warn_high: number | null;
  crit_high: number | null;
  warn_low: number | null;
  crit_low: number | null;
  state: SensorState;
}

export interface MachineSummary {
  id: string;
  machine_id: number;
  age: number;
  model: string;
  line_id: string;
  plant_id: string;
  position: number;
  status: MachineStatus;
  sensors: SensorReading[];
}

export type Severity = 'warning' | 'critical';
export type AlarmStatus = 'open' | 'acknowledged' | 'resolved';

export interface Alarm {
  id: string;
  machine_id: string;
  sensor_type: string | null;
  severity: Severity;
  value: number | null;
  threshold: number | null;
  status: AlarmStatus;
  message: string | null;
  ts: string | null;
  similar_failures?: { failure_id: string; score: number }[];
}

export interface MachineDetail {
  machine: {
    id: string;
    status: MachineStatus;
    age: number;
    model: string;
    line: string | null;
    position_in_line: number | null;
    plant_id: string | null;
    plant: string | null;
  };
  sensors: SensorReading[];
  open_alarms: {
    alarm: string;
    severity: Severity;
    sensor: string;
    value: number;
    threshold: number;
    raised_at: string;
    message: string;
    similar: { failure: string; score: number }[];
  }[];
  components: { type: string; component: string; last_replaced: string | null }[];
  recent_maintenance: { ts: string; component: string; reason: string; technician: string | null }[];
  downstream: NeighbourMachine[];
  upstream: NeighbourMachine[];
}

export interface NeighbourMachine {
  machine: string;
  status: MachineStatus;
  line: string | null;
  hops: number;
}

export interface GraphNode {
  id: string;
  label: string;
  caption: string;
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
}

export interface GraphView {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
}

export type TelemetrySeries = Record<SensorName, { ts: string; value: number }[]>;
