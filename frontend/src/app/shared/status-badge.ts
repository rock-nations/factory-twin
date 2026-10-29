import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { MachineStatus } from '../core/models/factory';

@Component({
  selector: 'app-status-badge',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="badge" [class]="status()">{{ label() }}</span>`,
  styles: `
    .badge {
      display: inline-block;
      padding: 1px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      border: 1px solid currentColor;
    }
    .running { color: var(--ft-running); }
    .warning { color: var(--ft-warning); }
    .alarm { color: var(--ft-alarm); }
    .down { color: var(--ft-down); }
  `,
})
export class StatusBadge {
  readonly status = input.required<MachineStatus>();
  protected readonly label = computed(() => this.status());
}
