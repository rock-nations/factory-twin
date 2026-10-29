import { Injectable, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { catchError, map, of, startWith, switchMap, timer } from 'rxjs';

import { HealthResponse } from '../models/health';
import { ApiService } from '../services/api.service';

export type BackendState = 'checking' | 'ok' | 'degraded' | 'unreachable';

export interface BackendHealth {
  state: BackendState;
  health: HealthResponse | null;
}

const POLL_MS = 10_000;

@Injectable({ providedIn: 'root' })
export class HealthStore {
  private readonly api = inject(ApiService);

  readonly backend = toSignal(
    timer(0, POLL_MS).pipe(
      switchMap(() =>
        this.api.getHealth().pipe(
          map((health): BackendHealth => ({ state: health.status, health })),
          catchError(() => of<BackendHealth>({ state: 'unreachable', health: null })),
        ),
      ),
      startWith<BackendHealth>({ state: 'checking', health: null }),
    ),
    { requireSync: true },
  );
}
