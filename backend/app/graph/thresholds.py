from dataclasses import dataclass
from typing import Literal

WARN_SIGMA = 2.5  # must match compute_thresholds

Level = Literal["normal", "warning", "critical"]


def classify_reading(
    value: float,
    warn_high: float | None,
    crit_high: float | None,
    warn_low: float | None = None,
    crit_low: float | None = None,
) -> Level:
    """Severity of a single reading against a sensor's thresholds (None = no such bound)."""
    if (crit_high is not None and value > crit_high) or (crit_low is not None and value < crit_low):
        return "critical"
    if (warn_high is not None and value > warn_high) or (warn_low is not None and value < warn_low):
        return "warning"
    return "normal"


@dataclass(frozen=True)
class SensorThresholds:
    """Per-sensor-type limits. warn/crit sit at mean + 2.5σ / + 3.5σ, so σ and the mean follow."""

    sensor: str
    unit: str
    warn_high: float
    crit_high: float
    warn_low: float | None = None
    crit_low: float | None = None

    @property
    def sigma(self) -> float:
        return self.crit_high - self.warn_high

    @property
    def mean(self) -> float:
        return self.warn_high - WARN_SIGMA * self.sigma

    def zscore(self, value: float) -> float:
        return (value - self.mean) / self.sigma

    def classify(self, value: float) -> Level:
        return classify_reading(value, self.warn_high, self.crit_high, self.warn_low, self.crit_low)

    def breached_threshold(self, value: float, level: Level) -> float:
        """The limit that `value` crossed for the given level."""
        high = self.crit_high if level == "critical" else self.warn_high
        low = self.crit_low if level == "critical" else self.warn_low
        if low is not None and value < low:
            return low
        return high
