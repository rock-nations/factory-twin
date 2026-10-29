import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { MatButtonModule } from '@angular/material/button';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Observable } from 'rxjs';

import { SENSOR_NAMES, SensorName } from '../../core/models/factory';
import { ApiService } from '../../core/services/api.service';
import { FactoryStore } from '../../core/state/factory.store';

@Component({
  selector: 'app-simulator-panel',
  imports: [FormsModule, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './simulator-panel.html',
  styleUrl: './simulator-panel.scss',
})
export class SimulatorPanel {
  private readonly api = inject(ApiService);
  private readonly snack = inject(MatSnackBar);
  protected readonly store = inject(FactoryStore);

  protected readonly status = this.store.simStatus;
  protected readonly running = computed(() => this.status().state === 'running');
  protected readonly sensors = SENSOR_NAMES;
  protected readonly machineIds = computed(() => this.store.machines().map((m) => m.id));

  protected readonly speed = signal(0.5);
  protected readonly machine = signal('M017');
  protected readonly sensor = signal<SensorName>('vibration');
  protected readonly magnitude = signal(4);
  protected readonly duration = signal(60);
  protected readonly confirmReset = signal(false);
  protected readonly busy = signal(false);

  protected readonly validMachine = computed(() => /^M\d{3}$/.test(this.machine()));

  protected start(): void {
    this.call(this.api.simStart(), 'Simulator started');
  }

  protected stop(): void {
    this.call(this.api.simStop(), 'Simulator paused');
  }

  protected applySpeed(): void {
    this.call(this.api.simSpeed(this.speed()), `Speed set to ${this.speed()} h/s`);
  }

  protected inject(): void {
    this.call(
      this.api.simInject({
        machine_id: this.machine().toUpperCase(),
        sensor: this.sensor(),
        magnitude: this.magnitude(),
        duration_s: this.duration(),
      }),
      `Injected ${this.magnitude()}σ ${this.sensor()} anomaly on ${this.machine().toUpperCase()}`,
    );
  }

  protected reset(): void {
    if (!this.confirmReset()) {
      this.confirmReset.set(true);
      setTimeout(() => this.confirmReset.set(false), 4000);
      return;
    }
    this.confirmReset.set(false);
    this.call(this.api.simReset(), 'Live state reset: alarms cleared, all machines running');
  }

  private call(request: Observable<unknown>, success: string): void {
    this.busy.set(true);
    request.subscribe({
      next: () => {
        this.busy.set(false);
        this.snack.open(success, undefined, { duration: 3000 });
      },
      error: (err: HttpErrorResponse) => {
        this.busy.set(false);
        const detail = typeof err.error?.detail === 'string' ? err.error.detail : err.message;
        this.snack.open(detail, 'Dismiss', { duration: 6000 });
      },
    });
  }
}
