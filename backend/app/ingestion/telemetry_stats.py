"""Sensor-deviation summaries over telemetry windows (docs + failure embeddings)."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.graph import constants as C

SENSORS = list(C.SENSOR_UNITS)
NOTABLE_MEAN_Z = 1.0
NOTABLE_PEAK_Z = 2.0


@dataclass(frozen=True)
class SensorWindow:
    sensor: str
    mean_z: float
    peak_z: float  # z with the largest magnitude
    mean: float


class TelemetryIndex:
    """Per-machine z-scored telemetry for fast window lookups."""

    def __init__(self, telemetry: pd.DataFrame) -> None:
        self.stats = {s: (float(telemetry[s].mean()), float(telemetry[s].std())) for s in SENSORS}
        df = telemetry.copy()
        df["datetime"] = pd.to_datetime(df["datetime"])
        self._ts: dict[int, np.ndarray] = {}
        self._raw: dict[int, np.ndarray] = {}
        self._z: dict[int, np.ndarray] = {}
        means = np.array([self.stats[s][0] for s in SENSORS])
        stds = np.array([self.stats[s][1] for s in SENSORS])
        for mid, g in df.sort_values("datetime").groupby("machineID"):
            raw = g[SENSORS].to_numpy(dtype=float)
            self._ts[int(str(mid))] = g["datetime"].to_numpy()
            self._raw[int(str(mid))] = raw
            self._z[int(str(mid))] = (raw - means) / stds

    def window(self, machine_id: int, end: pd.Timestamp, hours: int) -> list[SensorWindow]:
        """Readings in [end - hours, end)."""
        ts = self._ts.get(machine_id)
        if ts is None:
            return []
        lo = np.searchsorted(ts, np.datetime64(end - pd.Timedelta(hours=hours)), side="left")
        hi = np.searchsorted(ts, np.datetime64(end), side="left")
        if hi <= lo:
            return []
        z, raw = self._z[machine_id][lo:hi], self._raw[machine_id][lo:hi]
        out = []
        for j, s in enumerate(SENSORS):
            peak = float(z[np.argmax(np.abs(z[:, j])), j])
            out.append(SensorWindow(s, float(z[:, j].mean()), peak, float(raw[:, j].mean())))
        return out


def notable(window: list[SensorWindow]) -> list[SensorWindow]:
    return [w for w in window if abs(w.mean_z) >= NOTABLE_MEAN_Z or abs(w.peak_z) >= NOTABLE_PEAK_Z]


def describe_window(window: list[SensorWindow]) -> str:
    items = notable(window)
    if not items:
        return "no marked sensor deviations"
    return "; ".join(
        f"{w.sensor} averaged {w.mean_z:+.1f}σ (peak {w.peak_z:+.1f}σ, mean {w.mean:.1f} "
        f"{C.SENSOR_UNITS[w.sensor]})"
        for w in items
    )
