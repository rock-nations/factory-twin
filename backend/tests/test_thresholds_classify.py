from app.graph.thresholds import classify_reading


def test_high_side() -> None:
    assert classify_reading(10, 20, 30) == "normal"
    assert classify_reading(25, 20, 30) == "warning"
    assert classify_reading(31, 20, 30) == "critical"


def test_low_side_only_when_defined() -> None:
    assert classify_reading(-100, 20, 30) == "normal"
    assert classify_reading(5, 20, 30, 10, 2) == "warning"
    assert classify_reading(1, 20, 30, 10, 2) == "critical"


def test_boundary_is_not_a_breach() -> None:
    assert classify_reading(20, 20, 30) == "normal"
