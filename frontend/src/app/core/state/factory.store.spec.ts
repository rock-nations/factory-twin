import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { MachineSummary, PlantSummary, SensorReading } from '../models/factory';
import { SOCKET_FACTORY } from '../services/reconnecting-socket';
import { FakeSocket, fakeSocketFactory } from '../../testing';
import { FactoryStore, SPARK_POINTS } from './factory.store';

const limits = { warn_high: 52.5, crit_high: 57.5, warn_low: null, crit_low: null };

function sensor(name: SensorReading['sensor'], value: number | null = null): SensorReading {
  return { sensor: name, unit: 'x', value, as_of: null, avg_24h: null, state: 'no_data', ...limits };
}

export function machine(id: string, line: string, status: MachineSummary['status'] = 'running'): MachineSummary {
  return {
    id,
    machine_id: Number(id.slice(1)),
    age: 5,
    model: 'model1',
    line_id: line,
    plant_id: line.startsWith('A') ? 'P1' : 'P2',
    position: 1,
    status,
    sensors: [sensor('vibration')],
  };
}

const plants: PlantSummary[] = [
  {
    id: 'P1', name: 'Munich', location: 'DE', machine_count: 3, status_counts: { running: 3, warning: 0, alarm: 0, down: 0 },
    lines: [{ id: 'A1', name: 'Line A1', machine_count: 3, status_counts: { running: 3, warning: 0, alarm: 0, down: 0 } }],
  },
];

describe('FactoryStore', () => {
  let store: FactoryStore;
  let http: HttpTestingController;

  beforeEach(() => {
    FakeSocket.reset();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), { provide: SOCKET_FACTORY, useValue: fakeSocketFactory }],
    });
    store = TestBed.inject(FactoryStore);
    http = TestBed.inject(HttpTestingController);
  });

  function load(machines = [machine('M001', 'A1'), machine('M002', 'A1'), machine('M003', 'A1')]) {
    store.init();
    http.expectOne('/api/plants').flush(plants);
    http.expectOne('/api/machines').flush(machines);
    http.expectOne((r) => r.url === '/api/alarms' && r.params.get('status') === 'open').flush([]);
    http.expectOne((r) => r.url === '/api/alarms' && r.params.get('status') === 'acknowledged').flush([]);
    http.expectOne('/api/sim/status').flush({ state: 'paused', speed: 2, sim_time: null, loop: false });
  }

  it('loads structure, machines and the simulator state', () => {
    load();
    expect(store.loaded()).toBe(true);
    expect(store.machines()).toHaveLength(3);
    expect(store.simStatus().state).toBe('paused');
    expect(store.plants()[0].lines[0].machines.map((m) => m.id)).toEqual(['M001', 'M002', 'M003']);
    expect(store.totals()).toEqual({ running: 3, warning: 0, alarm: 0, down: 0 });
  });

  it('reports a load error', () => {
    store.init();
    http.expectOne('/api/plants').error(new ProgressEvent('error'));
    http.match(() => true);
    expect(store.loadError()).toContain('backend');
  });

  it('init is idempotent', () => {
    load();
    store.init();
    http.expectNone('/api/plants');
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('applies sensor updates: value, derived state and sparkline history', () => {
    load();
    store.apply({ type: 'sensor_update', sim_time: null, readings: [{ machine_id: 'M002', sensor: 'vibration', value: 60, ts: 't1' }] });
    const m = store.byId().get('M002')!;
    expect(m.sensors[0].value).toBe(60);
    expect(m.sensors[0].state).toBe('critical');
    expect(store.byId().get('M001')!.sensors[0].state).toBe('no_data');
    store.apply({ type: 'sensor_update', sim_time: null, readings: [{ machine_id: 'M002', sensor: 'vibration', value: 40, ts: 't2' }] });
    expect(store.byId().get('M002')!.sensors[0].state).toBe('normal');
    expect(store.vibrationHistory()['M002']).toEqual([60, 40]);
  });

  it('keeps only the last N points per sparkline', () => {
    load();
    for (let i = 0; i < SPARK_POINTS + 10; i++) {
      store.apply({ type: 'sensor_update', sim_time: null, readings: [{ machine_id: 'M001', sensor: 'vibration', value: i, ts: `t${i}` }] });
    }
    const h = store.vibrationHistory()['M001'];
    expect(h).toHaveLength(SPARK_POINTS);
    expect(h.at(-1)).toBe(SPARK_POINTS + 9);
  });

  it('publishes sensor ticks for charts', () => {
    load();
    const got: unknown[] = [];
    store.sensorTicks$.subscribe((t) => got.push(t));
    store.apply({ type: 'sensor_update', sim_time: null, readings: [{ machine_id: 'M001', sensor: 'volt', value: 1, ts: 't' }] });
    expect(got).toHaveLength(1);
  });

  it('updates machine status and the totals', () => {
    load();
    store.apply({ type: 'machine_status', machine_id: 'M001', status: 'alarm' });
    expect(store.totals().alarm).toBe(1);
    expect(store.plants()[0].counts.alarm).toBe(1);
    expect(store.plants()[0].lines[0].counts).toEqual({ running: 2, warning: 0, alarm: 1, down: 0 });
  });

  it('tracks alarm lifecycle and emits a toast event', () => {
    load();
    const toasts: string[] = [];
    store.alarmRaised$.subscribe((e) => toasts.push(e.alarm.id));
    const alarm = {
      id: 'AL-1', machine_id: 'M001', sensor_type: 'vibration', severity: 'critical' as const, value: 60,
      threshold: 57.5, status: 'open' as const, message: 'x', ts: 't', sigma: 4,
    };
    store.apply({ type: 'alarm_raised', escalated: false, alarm });
    expect(store.openAlarms().map((a) => a.id)).toEqual(['AL-1']);
    expect(toasts).toEqual(['AL-1']);

    store.apply({ type: 'alarm_acknowledged', alarm_id: 'AL-1', status: 'acknowledged' });
    expect(store.openAlarms()).toHaveLength(0);
    expect(store.alarms()[0].status).toBe('acknowledged');

    store.apply({ type: 'alarm_raised', escalated: true, alarm }); // escalation replaces, never duplicates
    expect(store.alarms()).toHaveLength(1);

    store.apply({ type: 'alarm_resolved', alarm_id: 'AL-1', machine_id: 'M001', sensor_type: 'vibration' });
    expect(store.alarms()).toHaveLength(0);
  });

  it('stores the simulator status from events', () => {
    load();
    store.apply({ type: 'sim_status', state: 'running', speed: 3, sim_time: '2015-01-01T06:00:00', loop: true });
    expect(store.simStatus()).toEqual({ state: 'running', speed: 3, sim_time: '2015-01-01T06:00:00', loop: true });
  });

  it('reloads everything after a live-state reset', () => {
    load();
    store.apply({ type: 'sensor_update', sim_time: null, readings: [{ machine_id: 'M001', sensor: 'vibration', value: 1, ts: 't' }] });
    store.apply({ type: 'live_state_reset' });
    expect(store.vibrationHistory()).toEqual({});
    http.expectOne('/api/plants').flush(plants);
    http.expectOne('/api/machines').flush([machine('M001', 'A1')]);
    http.match((r) => r.url === '/api/alarms').forEach((r) => r.flush([]));
    http.match(() => true);
    expect(store.machines()).toHaveLength(1);
  });

  it('consumes events from the socket', () => {
    load();
    FakeSocket.last.open();
    FakeSocket.last.receive({ type: 'machine_status', machine_id: 'M003', status: 'warning' });
    expect(store.byId().get('M003')!.status).toBe('warning');
  });
});
