from typing import Any

from app.graph.thresholds import SensorThresholds
from app.streaming.alarm_engine import AlarmEngine, AlarmRaised, AlarmResolved

# vibration: mean 40, σ 5 -> warn 52.5, crit 57.5 ; volt also has lows (mean 170, σ 15)
TH = {
    "vibration": SensorThresholds("vibration", "mm/s", 52.5, 57.5),
    "volt": SensorThresholds("volt", "V", 207.5, 222.5, 132.5, 117.5),
}
NORMAL, WARN, CRIT = 40.0, 55.0, 60.0


def feed(
    engine: AlarmEngine, values: list[float], sensor: str = "vibration", m: str = "M017"
) -> list[Any]:
    out = []
    for i, v in enumerate(values):
        out += engine.process(m, sensor, v, f"2026-01-01T00:00:{i:02d}")
    return out


def test_derived_sigma_and_mean() -> None:
    th = TH["vibration"]
    assert th.sigma == 5.0 and th.mean == 40.0 and th.zscore(55.0) == 3.0


def test_two_breaches_do_not_open_an_alarm() -> None:
    assert feed(AlarmEngine(TH), [WARN, WARN, NORMAL]) == []


def test_three_consecutive_breaches_open_a_warning_alarm() -> None:
    e = AlarmEngine(TH)
    actions = feed(e, [WARN, WARN, WARN])
    (raised,) = actions
    assert isinstance(raised, AlarmRaised) and raised.severity == "warning" and not raised.escalated
    assert raised.machine_id == "M017" and raised.threshold == 52.5 and raised.sigma == 3.0
    assert "3.0σ above normal" in raised.message
    assert e.machine_status("M017") == "warning"


def test_interrupted_run_restarts_the_count() -> None:
    assert feed(AlarmEngine(TH), [WARN, WARN, NORMAL, WARN, WARN]) == []


def test_critical_alarm_when_three_critical_readings() -> None:
    e = AlarmEngine(TH)
    (raised,) = feed(e, [CRIT, CRIT, CRIT])
    assert raised.severity == "critical" and raised.threshold == 57.5
    assert e.machine_status("M017") == "alarm"


def test_mixed_warning_then_critical_escalates_the_same_alarm() -> None:
    e = AlarmEngine(TH)
    actions = feed(e, [WARN, WARN, WARN, CRIT, CRIT, CRIT])
    assert [a.severity for a in actions if isinstance(a, AlarmRaised)] == ["warning", "critical"]
    first, second = actions
    assert first.alarm_id == second.alarm_id and second.escalated
    assert e.open_alarm_count() == 1


def test_no_duplicate_alarm_while_one_is_open() -> None:
    assert len(feed(AlarmEngine(TH), [WARN] * 12)) == 1


def test_clears_after_five_normal_readings_not_four() -> None:
    e = AlarmEngine(TH)
    feed(e, [WARN] * 3)
    assert feed(e, [NORMAL] * 4) == []
    assert e.machine_status("M017") == "warning"
    (resolved,) = feed(e, [NORMAL])
    assert isinstance(resolved, AlarmResolved) and e.machine_status("M017") == "running"


def test_breach_during_clearing_resets_the_normal_count() -> None:
    e = AlarmEngine(TH)
    feed(e, [WARN] * 3)
    assert feed(e, [NORMAL] * 4 + [WARN] + [NORMAL] * 4) == []
    assert len(feed(e, [NORMAL])) == 1


def test_can_alarm_again_after_resolution() -> None:
    e = AlarmEngine(TH)
    actions = feed(e, [WARN] * 3 + [NORMAL] * 5 + [WARN] * 3)
    raised = [a for a in actions if isinstance(a, AlarmRaised)]
    assert len(raised) == 2 and raised[0].alarm_id != raised[1].alarm_id


def test_low_side_breach_on_volt() -> None:
    e = AlarmEngine(TH)
    (raised,) = feed(e, [125.0] * 3, sensor="volt")
    assert raised.severity == "warning" and raised.threshold == 132.5 and raised.sigma < 0
    assert "below normal" in raised.message


def test_sensors_and_machines_are_independent() -> None:
    e = AlarmEngine(TH)
    feed(e, [WARN] * 3, "vibration", "M001")
    assert e.machine_status("M001") == "warning" and e.machine_status("M002") == "running"
    assert feed(e, [170.0] * 10, "volt", "M001") == []  # 170 V is normal


def test_machine_status_is_worst_open_alarm() -> None:
    e = AlarmEngine(TH)
    feed(e, [WARN] * 3, "vibration")
    feed(e, [100.0] * 3, "volt")
    assert e.machine_status("M017") == "alarm"


def test_seeded_alarm_can_be_resolved_after_restart() -> None:
    e = AlarmEngine(TH)
    e.seed_open_alarm("M017", "vibration", "AL-old", "critical")
    assert e.machine_status("M017") == "alarm"
    (resolved,) = feed(e, [NORMAL] * 5)
    assert isinstance(resolved, AlarmResolved) and resolved.alarm_id == "AL-old"


def test_unknown_sensor_is_ignored() -> None:
    assert AlarmEngine(TH).process("M001", "humidity", 99.0, "t") == []
