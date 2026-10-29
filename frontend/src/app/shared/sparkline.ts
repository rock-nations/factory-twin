import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

@Component({
  selector: 'app-sparkline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg [attr.viewBox]="'0 0 ' + width() + ' ' + height()" preserveAspectRatio="none" aria-hidden="true">
      @if (points()) {
        <polyline [attr.points]="points()" fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" />
      }
    </svg>
  `,
  styles: `
    :host { display: block; line-height: 0; }
    svg { width: 100%; height: 100%; }
  `,
})
export class Sparkline {
  readonly values = input<number[]>([]);
  readonly width = input(80);
  readonly height = input(22);

  protected readonly points = computed(() => {
    const v = this.values();
    if (v.length < 2) return '';
    const lo = Math.min(...v);
    const hi = Math.max(...v);
    const span = hi - lo || 1;
    const w = this.width();
    const h = this.height();
    return v
      .map((y, i) => `${((i / (v.length - 1)) * w).toFixed(1)},${(h - 2 - ((y - lo) / span) * (h - 4)).toFixed(1)}`)
      .join(' ');
  });
}
