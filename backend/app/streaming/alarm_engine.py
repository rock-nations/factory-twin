"""Debounced threshold alarms (spec §7.2): 3 consecutive breaches open, 5 normal readings clear.

Pure state machine: no I/O, so it is unit-testable and deterministic.
"""

from dataclasses import dataclass, field
from typing import Literal

from app.graph.thresholds import Level, SensorThresholds

BREACHES_TO_OPEN = 3
NORMALS_TO_CLEAR = 5

Severity = Literal["warning", "critical"]
MachineStatus = Literal["running", "warning", "alarm"]


@dataclass
class OpenAlarm:
    alarm_id: str
    severity: Severity


@dataclass
class _SensorState:
    warn_run: int = 0  # consecutive readings at warning level or worse
    crit_run: int = 0  # consecutive readings at critical level
    normal_run: int = 0
    alarm: OpenAlarm | None = None


@dataclass(frozen=True)
class AlarmRaised:
    alarm_id: str
    machine_id: str
    sensor: str
    severity: Severity
    value: float
    threshold: float
    sigma: float  # deviation of the triggering reading, in σ
    ts: str
    message: str
    escalated: bool = False  # True when an open warning alarm became critical


@dataclass(frozen=True)
class AlarmResolved:
    alarm_id: str
    machine_id: str
    sensor: str
    ts: str


AlarmAction = AlarmRaised | AlarmResolved


@dataclass
class AlarmEngine:
    thresholds: dict[str, SensorThresholds]
    breaches_to_open: int = BREACHES_TO_OPEN
    normals_to_clear: int = NORMALS_TO_CLEAR
    _state: dict[tuple[str, str], _SensorState] = field(default_factory=dict)
    _seq: int = 0

    def seed_open_alarm(
        self, machine_id: str, sensor: str, alarm_id: str, severity: Severity
    ) -> None:
        """Re-adopt an alarm that is still open in the graph (e.g. after a backend restart)."""
        self._state.setdefault((machine_id, sensor), _SensorState()).alarm = OpenAlarm(
            alarm_id, severity
        )

    def reset(self) -> None:
        self._state.clear()

    def process(self, machine_id: str, sensor: str, value: float, ts: str) -> list[AlarmAction]:
        th = self.thresholds.get(sensor)
        if th is None:
            return []
        st = self._state.setdefault((machine_id, sensor), _SensorState())
        level: Level = th.classify(value)
        actions: list[AlarmAction] = []

        if level == "normal":
            st.warn_run = st.crit_run = 0
            st.normal_run += 1
            if st.alarm and st.normal_run >= self.normals_to_clear:
                actions.append(AlarmResolved(st.alarm.alarm_id, machine_id, sensor, ts))
                st.alarm = None
                st.normal_run = 0
            return actions

        st.normal_run = 0
        st.warn_run += 1
        st.crit_run = st.crit_run + 1 if level == "critical" else 0

        if st.alarm is None:
            if st.warn_run >= self.breaches_to_open:
                severity: Severity = (
                    "critical" if st.crit_run >= self.breaches_to_open else "warning"
                )
                self._seq += 1
                alarm_id = (
                    f"AL-{machine_id}-{sensor}-{ts.replace(':', '').replace('-', '')}-{self._seq}"
                )
                st.alarm = OpenAlarm(alarm_id, severity)
                actions.append(self._raised(st.alarm, machine_id, sensor, th, value, ts, False))
        elif st.alarm.severity == "warning" and st.crit_run >= self.breaches_to_open:
            st.alarm.severity = "critical"
            actions.append(self._raised(st.alarm, machine_id, sensor, th, value, ts, True))
        return actions

    @staticmethod
    def _raised(
        alarm: OpenAlarm,
        machine_id: str,
        sensor: str,
        th: SensorThresholds,
        value: float,
        ts: str,
        escalated: bool,
    ) -> AlarmRaised:
        level: Level = alarm.severity
        threshold = th.breached_threshold(value, level)
        sigma = th.zscore(value)
        message = (
            f"{machine_id} {sensor} at {value:.1f} {th.unit} is {abs(sigma):.1f}σ "
            f"{'above' if sigma >= 0 else 'below'} normal ({alarm.severity}, limit {threshold:.1f})"
        )
        return AlarmRaised(
            alarm.alarm_id, machine_id, sensor, alarm.severity, value, threshold, sigma, ts,
            message, escalated,
        )  # fmt: skip

    def machine_status(self, machine_id: str) -> MachineStatus:
        severities = [
            st.alarm.severity
            for (m, _), st in self._state.items()
            if m == machine_id and st.alarm is not None
        ]
        if "critical" in severities:
            return "alarm"
        return "warning" if severities else "running"

    def open_alarm_count(self) -> int:
        return sum(1 for st in self._state.values() if st.alarm is not None)
