import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { describe, expect, it } from 'vitest';

import { FactoryStore, PlantView } from '../../core/state/factory.store';
import { Dashboard } from './dashboard';

const plants: PlantView[] = [
  {
    id: 'P1', name: 'Munich Assembly', location: 'Munich', counts: { running: 1, warning: 0, alarm: 1, down: 0 },
    lines: [
      {
        id: 'A1', name: 'Line A1', counts: { running: 1, warning: 0, alarm: 1, down: 0 },
        machines: [
          { id: 'M001', machine_id: 1, age: 3, model: 'model1', line_id: 'A1', plant_id: 'P1', position: 1, status: 'running', sensors: [{ sensor: 'vibration', unit: 'mm/s', value: 41.23, as_of: null, avg_24h: null, warn_high: 52.5, crit_high: 57.5, warn_low: null, crit_low: null, state: 'normal' }] },
          { id: 'M002', machine_id: 2, age: 3, model: 'model2', line_id: 'A1', plant_id: 'P1', position: 2, status: 'alarm', sensors: [] },
        ],
      },
    ],
  },
];

describe('Dashboard', () => {
  it('renders status totals, plants, lines and one tile per machine', () => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        {
          provide: FactoryStore,
          useValue: {
            plants: signal(plants),
            totals: signal({ running: 99, warning: 0, alarm: 1, down: 0 }),
            simStatus: signal({ state: 'running', speed: 0.5, sim_time: '2015-03-02T10:00:00', loop: false }),
            vibrationHistory: signal({ M001: [40, 41, 42] }),
            loaded: signal(true),
            loadError: signal(null),
          },
        },
      ],
    });
    const f = TestBed.createComponent(Dashboard);
    f.detectChanges();
    const root = f.nativeElement as HTMLElement;

    expect(root.querySelector('[data-testid="totals"]')?.textContent).toMatch(/99\s*running/);
    expect(root.textContent).toContain('Munich Assembly');
    const alarmTile = root.querySelector('[data-testid="tile-M002"]')!;
    expect(alarmTile.classList.contains('alarm')).toBe(true);
    expect(alarmTile.getAttribute('href')).toBe('/machines/M002');
    const ok = root.querySelector('[data-testid="tile-M001"]')!;
    expect(ok.textContent).toContain('41.2');
    expect(ok.querySelector('polyline')).not.toBeNull();
    expect(root.querySelector('[data-testid="sim-summary"]')?.textContent).toContain('simulator running');
    expect(root.querySelector('[data-testid="sim-summary"]')?.textContent).toContain('2015-03-02 10:00');
  });
});
