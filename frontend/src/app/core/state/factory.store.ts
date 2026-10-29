import { Injectable, computed, inject, signal } from '@angular/core';
import { Subject, forkJoin } from 'rxjs';

import { Alarm, MachineStatus, MachineSummary, PlantSummary, SensorName, StatusCounts } from '../models/factory';
import { AlarmRaisedEvent, LiveEvent, SensorUpdateEvent } from '../models/events';
import { SimStatus } from '../models/sim';
import { ApiService } from '../services/api.service';
import { EventsSocketService } from '../services/events-socket.service';
import { classifyReading } from '../util/thresholds';

export const SPARK_POINTS = 30;
const EMPTY_COUNTS = (): StatusCounts => ({ running: 0, warning: 0, alarm: 0, down: 0 });

export interface LineView {
  id: string;
  name: string;
  machines: MachineSummary[];
  counts: StatusCounts;
}

export interface PlantView {
  id: string;
  name: string;
  location: string | null;
  lines: LineView[];
  counts: StatusCounts;
}

export interface SensorTick {
  machine_id: string;
  sensor: SensorName;
  value: number;
  ts: string;
}

@Injectable({ providedIn: 'root' })
export class FactoryStore {
  private readonly api = inject(ApiService);
  private readonly events = inject(EventsSocketService);
  private started = false;

  readonly machines = signal<MachineSummary[]>([]);
  readonly structure = signal<PlantSummary[]>([]);
  readonly alarms = signal<Alarm[]>([]);
  readonly simStatus = signal<SimStatus>({ state: 'offline', speed: 0.5, sim_time: null, loop: false });
  readonly loaded = signal(false);
  readonly loadError = signal<string | null>(null);
  /** last vibration values per machine, for the dashboard sparklines */
  readonly vibrationHistory = signal<Record<string, number[]>>({});

  readonly alarmRaised$ = new Subject<AlarmRaisedEvent>();
  readonly sensorTicks$ = new Subject<SensorTick[]>();

  readonly byId = computed(() => new Map(this.machines().map((m) => [m.id, m])));
  readonly openAlarms = computed(() => this.alarms().filter((a) => a.status === 'open'));

  readonly totals = computed(() => {
    const counts = EMPTY_COUNTS();
    for (const m of this.machines()) counts[m.status]++;
    return counts;
  });

  readonly plants = computed<PlantView[]>(() => {
    const machines = this.machines();
    return this.structure().map((p) => {
      const lines = p.lines.map((l): LineView => {
        const ms = machines.filter((m) => m.line_id === l.id);
        const counts = EMPTY_COUNTS();
        for (const m of ms) counts[m.status]++;
        return { id: l.id, name: l.name, machines: ms, counts };
      });
      const counts = EMPTY_COUNTS();
      for (const l of lines) for (const k of Object.keys(counts) as MachineStatus[]) counts[k] += l.counts[k];
      return { id: p.id, name: p.name, location: p.location, lines, counts };
    });
  });

  /** Idempotent: loads data and starts listening to live events. */
  init(): void {
    if (this.started) return;
    this.started = true;
    this.events.events$.subscribe((e) => this.apply(e));
    this.events.connect();
    this.reload();
  }

  reload(): void {
    this.loadError.set(null);
    forkJoin({
      plants: this.api.getPlants(),
      machines: this.api.getMachines(),
      open: this.api.getAlarms('open'),
      acked: this.api.getAlarms('acknowledged'),
    }).subscribe({
      next: ({ plants, machines, open, acked }) => {
        this.structure.set(plants);
        this.machines.set(machines);
        this.alarms.set([...open, ...acked]);
        this.loaded.set(true);
      },
      error: () => this.loadError.set('Could not load the factory. Is the backend running?'),
    });
    this.api.getSimStatus().subscribe({ next: (s) => this.simStatus.set(s), error: () => undefined });
  }

  apply(event: LiveEvent): void {
    switch (event.type) {
      case 'sensor_update':
        this.applyReadings(event);
        break;
      case 'machine_status':
        this.patchMachine(event.machine_id, (m) => ({ ...m, status: event.status }));
        break;
      case 'alarm_raised': {
        const alarm = { ...event.alarm, status: 'open' as const };
        this.alarms.update((list) => [alarm, ...list.filter((a) => a.id !== alarm.id)]);
        this.alarmRaised$.next(event);
        break;
      }
      case 'alarm_resolved':
        this.alarms.update((list) => list.filter((a) => a.id !== event.alarm_id));
        break;
      case 'alarm_acknowledged':
        this.alarms.update((list) =>
          list.map((a) => (a.id === event.alarm_id ? { ...a, status: 'acknowledged' as const } : a)),
        );
        break;
      case 'sim_status': {
        const { type: _type, ...status } = event;
        this.simStatus.set(status);
        break;
      }
      case 'live_state_reset':
        this.vibrationHistory.set({});
        this.reload();
        break;
    }
  }

  private patchMachine(id: string, fn: (m: MachineSummary) => MachineSummary): void {
    this.machines.update((list) => list.map((m) => (m.id === id ? fn(m) : m)));
  }

  private applyReadings(event: SensorUpdateEvent): void {
    const byMachine = new Map<string, SensorUpdateEvent['readings']>();
    for (const r of event.readings) byMachine.set(r.machine_id, [...(byMachine.get(r.machine_id) ?? []), r]);

    this.machines.update((list) =>
      list.map((m) => {
        const updates = byMachine.get(m.id);
        if (!updates) return m;
        return {
          ...m,
          sensors: m.sensors.map((s) => {
            const u = updates.find((r) => r.sensor === s.sensor);
            return u ? { ...s, value: u.value, as_of: u.ts, state: classifyReading(u.value, s) } : s;
          }),
        };
      }),
    );

    this.vibrationHistory.update((history) => {
      const next = { ...history };
      for (const r of event.readings) {
        if (r.sensor !== 'vibration') continue;
        next[r.machine_id] = [...(next[r.machine_id] ?? []), r.value].slice(-SPARK_POINTS);
      }
      return next;
    });
    this.sensorTicks$.next(event.readings);
  }
}
