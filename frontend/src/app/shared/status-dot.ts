import { ChangeDetectionStrategy, Component, input } from '@angular/core';

export type Tone = 'ok' | 'warn' | 'error' | 'idle';

@Component({
  selector: 'app-status-dot',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="dot" [class]="tone()"></span>`,
  styles: `
    .dot {
      display: inline-block;
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--ft-down);
    }
    .ok { background: var(--ft-running); }
    .warn { background: var(--ft-warning); }
    .error { background: var(--ft-alarm); }
  `,
})
export class StatusDot {
  readonly tone = input<Tone>('idle');
}
