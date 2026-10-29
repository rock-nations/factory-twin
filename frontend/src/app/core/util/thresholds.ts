import { SensorReading, SensorState } from '../models/factory';

type Limits = Pick<SensorReading, 'warn_high' | 'crit_high' | 'warn_low' | 'crit_low'>;

/** Same rule as the backend's classify_reading: a bound that is null does not exist. */
export function classifyReading(value: number | null, t: Limits): SensorState {
  if (value === null || value === undefined) return 'no_data';
  if ((t.crit_high !== null && value > t.crit_high) || (t.crit_low !== null && value < t.crit_low)) {
    return 'critical';
  }
  if ((t.warn_high !== null && value > t.warn_high) || (t.warn_low !== null && value < t.warn_low)) {
    return 'warning';
  }
  return 'normal';
}
