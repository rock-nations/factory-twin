"""In-memory ring buffer of recent readings per sensor (raw telemetry never goes into Neo4j)."""

from collections import defaultdict, deque

from app.graph import constants as C

RING_SIZE = 200
AVG_WINDOW = 24  # readings; one reading = one simulated hour -> a rolling 24 h average


class TelemetryBuffer:
    def __init__(self, size: int = RING_SIZE) -> None:
        self._size = size
        self._data: dict[tuple[str, str], deque[tuple[str, float]]] = defaultdict(
            lambda: deque(maxlen=size)
        )

    def add(self, machine_id: str, sensor: str, ts: str, value: float) -> None:
        self._data[(machine_id, sensor)].append((ts, value))

    def values(self, machine_id: str, sensor: str, last: int | None = None) -> list[float]:
        items = self._data.get((machine_id, sensor))
        if not items:
            return []
        vals = [v for _, v in items]
        return vals[-last:] if last else vals

    def rolling_avg(self, machine_id: str, sensor: str, window: int = AVG_WINDOW) -> float | None:
        vals = self.values(machine_id, sensor, window)
        return sum(vals) / len(vals) if vals else None

    def series(self, machine_id: str) -> dict[str, list[dict[str, float | str]]]:
        return {
            sensor: [{"ts": ts, "value": v} for ts, v in self._data.get((machine_id, sensor), [])]
            for sensor in C.SENSOR_UNITS
        }

    def clear(self) -> None:
        self._data.clear()
