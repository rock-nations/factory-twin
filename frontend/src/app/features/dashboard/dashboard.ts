import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { MachineStatus, MachineSummary } from '../../core/models/factory';
import { FactoryStore, SPARK_POINTS } from '../../core/state/factory.store';
import { Sparkline } from '../../shared/sparkline';

const STATUS_ORDER: MachineStatus[] = ['running', 'warning', 'alarm', 'down'];

@Component({
  selector: 'app-dashboard',
  imports: [RouterLink, Sparkline, DecimalPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
})
export class Dashboard {
  protected readonly store = inject(FactoryStore);
  protected readonly statuses = STATUS_ORDER;
  protected readonly sparkPoints = SPARK_POINTS;

  protected vibration(m: MachineSummary): number | null {
    return m.sensors.find((s) => s.sensor === 'vibration')?.value ?? null;
  }

  protected trackById(_: number, m: MachineSummary): string {
    return m.id;
  }
}
