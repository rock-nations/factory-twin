import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { SimState } from '../../core/models/sim';
import { ApiService } from '../../core/services/api.service';
import { FactoryStore } from '../../core/state/factory.store';
import { SimulatorPanel } from './simulator-panel';

describe('SimulatorPanel', () => {
  const state = signal<SimState>('paused');
  const api = {
    simStart: vi.fn(() => of({})),
    simStop: vi.fn(() => of({})),
    simSpeed: vi.fn(() => of({})),
    simInject: vi.fn(() => of({})),
    simReset: vi.fn(() => of({ alarms_deleted: 1, simulator_errors_deleted: 0, machines_reset: 100, sensors_cleared: 400 })),
  };
  const snack = { open: vi.fn() };

  function setup(s: SimState) {
    state.set(s);
    TestBed.configureTestingModule({
      providers: [
        { provide: ApiService, useValue: api },
        { provide: MatSnackBar, useValue: snack },
        {
          provide: FactoryStore,
          useValue: {
            simStatus: signal({ state: s, speed: 0.5, sim_time: null, loop: false }),
            machines: signal([{ id: 'M017' }, { id: 'M018' }]),
          },
        },
      ],
    });
    const f = TestBed.createComponent(SimulatorPanel);
    f.detectChanges();
    const q = (id: string) => (f.nativeElement as HTMLElement).querySelector(`[data-testid="${id}"]`) as HTMLButtonElement;
    return { f, q };
  }

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.resetTestingModule();
  });

  it('while running: stop and inject are enabled, start and reset are not', () => {
    const { q } = setup('running');
    expect(q('sim-state').textContent).toBe('running');
    expect(q('start').disabled).toBe(true);
    expect(q('stop').disabled).toBe(false);
    expect(q('inject').disabled).toBe(false);
    expect(q('reset').disabled).toBe(true);
  });

  it('while stopped: reset is enabled and inject is not', () => {
    const { q } = setup('paused');
    expect(q('reset').disabled).toBe(false);
    expect(q('inject').disabled).toBe(true);
    expect(q('start').disabled).toBe(false);
    expect(q('stop').disabled).toBe(true);
  });

  it('start and stop call the API and confirm with a snackbar', () => {
    const a = setup('paused');
    a.q('start').click();
    expect(api.simStart).toHaveBeenCalled();
    expect(snack.open).toHaveBeenCalledWith('Simulator started', undefined, { duration: 3000 });
  });

  it('injects with the form values (defaults: M017, vibration, 4σ, 60 s)', () => {
    const { q } = setup('running');
    q('inject').click();
    expect(api.simInject).toHaveBeenCalledWith({ machine_id: 'M017', sensor: 'vibration', magnitude: 4, duration_s: 60 });
  });

  it('reset needs a second click to confirm', () => {
    const { f, q } = setup('paused');
    q('reset').click();
    expect(api.simReset).not.toHaveBeenCalled();
    f.detectChanges();
    expect(q('reset').textContent).toContain('Click again to confirm');
    q('reset').click();
    expect(api.simReset).toHaveBeenCalledTimes(1);
  });

  it('shows the server message when a call fails', () => {
    api.simStart.mockReturnValueOnce(throwError(() => ({ error: { detail: 'MQTT broker unreachable' }, message: 'x' })));
    const { q } = setup('paused');
    q('start').click();
    expect(snack.open).toHaveBeenCalledWith('MQTT broker unreachable', 'Dismiss', { duration: 6000 });
  });
});
