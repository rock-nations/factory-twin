import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MAT_SNACK_BAR_DATA, MatSnackBarRef } from '@angular/material/snack-bar';
import { Router } from '@angular/router';

import { AlarmRaisedEvent } from '../core/models/events';
import { ChatStore } from '../core/state/chat.store';

@Component({
  selector: 'app-alarm-snackbar',
  imports: [MatButtonModule, MatIconModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="toast" [class]="alarm.severity" data-testid="alarm-toast">
      <mat-icon>{{ alarm.severity === 'critical' ? 'error' : 'warning' }}</mat-icon>
      <div class="body">
        <strong>{{ alarm.machine_id }} · {{ alarm.sensor_type }} {{ alarm.severity }}@if (data.escalated) { (escalated) }</strong>
        <span>{{ alarm.message }}</span>
      </div>
      <div class="actions">
        <button mat-button (click)="ask()">Ask about this</button>
        <button mat-button (click)="open()">Open</button>
      </div>
    </div>
  `,
  styles: `
    .toast { display: flex; align-items: center; gap: 12px; min-width: 340px; }
    .toast.critical mat-icon { color: var(--ft-alarm); }
    .toast.warning mat-icon { color: var(--ft-warning); }
    .body { display: flex; flex-direction: column; flex: 1; font-size: 12px; }
    .body strong { font-size: 13px; }
    .actions { display: flex; flex-direction: column; }
  `,
})
export class AlarmSnackbar {
  protected readonly data = inject<AlarmRaisedEvent>(MAT_SNACK_BAR_DATA);
  protected readonly alarm = this.data.alarm;
  private readonly ref = inject(MatSnackBarRef);
  private readonly chat = inject(ChatStore);
  private readonly router = inject(Router);

  ask(): void {
    this.chat.prefill(`Diagnose ${this.alarm.machine_id}: ${this.alarm.sensor_type} alarm`);
    this.ref.dismiss();
  }

  open(): void {
    void this.router.navigate(['/machines', this.alarm.machine_id]);
    this.ref.dismiss();
  }
}
