import { Alarm, MachineStatus, SensorName } from './factory';
import { SimStatus } from './sim';

export interface SensorUpdateEvent {
  type: 'sensor_update';
  sim_time: string | null;
  readings: { machine_id: string; sensor: SensorName; value: number; ts: string }[];
}

export interface AlarmRaisedEvent {
  type: 'alarm_raised';
  escalated: boolean;
  alarm: Alarm & { sigma: number };
}

export interface AlarmResolvedEvent {
  type: 'alarm_resolved';
  alarm_id: string;
  machine_id: string;
  sensor_type: string;
}

export interface AlarmAckEvent {
  type: 'alarm_acknowledged';
  alarm_id: string;
  status: string;
}

export interface MachineStatusEvent {
  type: 'machine_status';
  machine_id: string;
  status: MachineStatus;
}

export type SimStatusEvent = { type: 'sim_status' } & SimStatus;
export interface ResetEvent {
  type: 'live_state_reset';
}

export type LiveEvent =
  | SensorUpdateEvent
  | AlarmRaisedEvent
  | AlarmResolvedEvent
  | AlarmAckEvent
  | MachineStatusEvent
  | SimStatusEvent
  | ResetEvent;
