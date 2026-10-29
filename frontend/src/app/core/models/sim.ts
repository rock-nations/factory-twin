import { SensorName } from './factory';

export type SimState = 'running' | 'paused' | 'finished' | 'offline';

export interface SimStatus {
  state: SimState;
  speed: number;
  sim_time: string | null;
  loop: boolean;
  mqtt_connected?: boolean;
}

export interface InjectRequest {
  machine_id: string;
  sensor: SensorName;
  magnitude: number;
  duration_s: number;
}

export interface ResetSummary {
  alarms_deleted: number;
  simulator_errors_deleted: number;
  machines_reset: number;
  sensors_cleared: number;
}
