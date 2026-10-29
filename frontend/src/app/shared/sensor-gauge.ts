import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { DecimalPipe } from '@angular/common';

import { SensorReading } from '../core/models/factory';

const SIGMAS_BELOW = 4;
const SIGMAS_ABOVE = 5;

/** Scale in σ units around the mean so every sensor shares one visual range. */
export function gaugeGeometry(s: SensorReading): { value: number | null; warnHigh: number; critHigh: number; warnLow: number | null; critLow: number | null } | null {
  if (s.warn_high === null || s.crit_high === null) return null;
  const sigma = s.crit_high - s.warn_high;
  const mean = s.warn_high - 2.5 * sigma;
  const lo = mean - SIGMAS_BELOW * sigma;
  const span = (SIGMAS_BELOW + SIGMAS_ABOVE) * sigma;
  const pct = (v: number) => Math.min(100, Math.max(0, ((v - lo) / span) * 100));
  return {
    value: s.value === null ? null : pct(s.value),
    warnHigh: pct(s.warn_high),
    critHigh: pct(s.crit_high),
    warnLow: s.warn_low === null ? null : pct(s.warn_low),
    critLow: s.crit_low === null ? null : pct(s.crit_low),
  };
}

@Component({
  selector: 'app-sensor-gauge',
  imports: [DecimalPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="head">
      <span class="name">{{ sensor().sensor }}</span>
      <span class="val mono" [class]="sensor().state">
        @if (sensor().value !== null) {
          {{ sensor().value | number: '1.1-1' }} <small>{{ sensor().unit }}</small>
        } @else {
          — <small>no data</small>
        }
      </span>
    </div>
    @if (geo(); as g) {
      <div class="track" role="img" [attr.aria-label]="sensor().sensor + ' ' + sensor().state">
        <span class="zone warn" [style.left.%]="g.warnHigh" [style.width.%]="g.critHigh - g.warnHigh"></span>
        <span class="zone crit" [style.left.%]="g.critHigh" [style.width.%]="100 - g.critHigh"></span>
        @if (g.warnLow !== null && g.critLow !== null) {
          <span class="zone warn" [style.left.%]="g.critLow" [style.width.%]="g.warnLow - g.critLow"></span>
          <span class="zone crit" [style.left.%]="0" [style.width.%]="g.critLow"></span>
        }
        @if (g.value !== null) {
          <span class="needle" [class]="sensor().state" [style.left.%]="g.value"></span>
        }
      </div>
    }
  `,
  styles: `
    :host { display: block; }
    .head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
    .name { text-transform: capitalize; color: var(--ft-text-dim); font-size: 12px; }
    .val { font-size: 18px; font-weight: 500; }
    .val small { font-size: 11px; color: var(--ft-text-dim); font-weight: 400; }
    .val.warning { color: var(--ft-warning); }
    .val.critical { color: var(--ft-alarm); }
    .track { position: relative; height: 8px; border-radius: 4px; background: var(--ft-surface-2); overflow: hidden; }
    .zone { position: absolute; top: 0; bottom: 0; }
    .zone.warn { background: color-mix(in srgb, var(--ft-warning) 35%, transparent); }
    .zone.crit { background: color-mix(in srgb, var(--ft-alarm) 40%, transparent); }
    .needle { position: absolute; top: -1px; bottom: -1px; width: 3px; margin-left: -1px; border-radius: 2px; background: var(--ft-running); }
    .needle.warning { background: var(--ft-warning); }
    .needle.critical { background: var(--ft-alarm); }
  `,
})
export class SensorGauge {
  readonly sensor = input.required<SensorReading>();
  protected readonly geo = computed(() => gaugeGeometry(this.sensor()));
}
