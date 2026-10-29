import { describe, expect, it } from 'vitest';

import { classifyReading } from './thresholds';

const high = { warn_high: 20, crit_high: 30, warn_low: null, crit_low: null };

describe('classifyReading', () => {
  it('matches the backend rule', () => {
    expect(classifyReading(10, high)).toBe('normal');
    expect(classifyReading(20, high)).toBe('normal'); // boundary is not a breach
    expect(classifyReading(25, high)).toBe('warning');
    expect(classifyReading(31, high)).toBe('critical');
  });

  it('only checks lows that exist', () => {
    expect(classifyReading(-500, high)).toBe('normal');
    const both = { ...high, warn_low: 10, crit_low: 2 };
    expect(classifyReading(5, both)).toBe('warning');
    expect(classifyReading(1, both)).toBe('critical');
  });

  it('reports no_data for null', () => {
    expect(classifyReading(null, high)).toBe('no_data');
  });
});
