import { describe, expect, it } from 'vitest';

import { SensorReading } from '../core/models/factory';
import { gaugeGeometry } from './sensor-gauge';

const base: SensorReading = {
  sensor: 'vibration', unit: 'mm/s', value: 40, as_of: null, avg_24h: null, state: 'normal',
  warn_high: 52.5, crit_high: 57.5, warn_low: null, crit_low: null,
};

describe('gaugeGeometry', () => {
  it('places the mean 4/9 along a -4σ..+5σ scale, with warn/crit markers further right', () => {
    const g = gaugeGeometry(base)!;
    expect(g.value).toBeCloseTo((4 / 9) * 100, 5);
    expect(g.warnHigh).toBeCloseTo((6.5 / 9) * 100, 5);
    expect(g.critHigh).toBeCloseTo((7.5 / 9) * 100, 5);
    expect(g.warnLow).toBeNull();
  });

  it('clamps readings far outside the scale', () => {
    expect(gaugeGeometry({ ...base, value: 999 })!.value).toBe(100);
    expect(gaugeGeometry({ ...base, value: -999 })!.value).toBe(0);
  });

  it('has no needle without data and no gauge without thresholds', () => {
    expect(gaugeGeometry({ ...base, value: null })!.value).toBeNull();
    expect(gaugeGeometry({ ...base, warn_high: null })).toBeNull();
  });

  it('includes low thresholds when present', () => {
    const g = gaugeGeometry({ ...base, warn_low: 27.5, crit_low: 22.5 })!;
    expect(g.warnLow).toBeCloseTo((1.5 / 9) * 100, 5);
    expect(g.critLow).toBeCloseTo((0.5 / 9) * 100, 5);
  });
});
