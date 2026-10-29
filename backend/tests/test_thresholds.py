import pandas as pd
import pytest

from app.ingestion.compute_thresholds import compute_thresholds, thresholds_from_stats


def test_high_and_low_thresholds_for_volt() -> None:
    t = thresholds_from_stats("volt", 100.0, 10.0)
    assert t["warn_high"] == pytest.approx(125.0)
    assert t["crit_high"] == pytest.approx(135.0)
    assert t["warn_low"] == pytest.approx(75.0)
    assert t["crit_low"] == pytest.approx(65.0)


def test_vibration_has_no_low_thresholds() -> None:
    t = thresholds_from_stats("vibration", 40.0, 5.0)
    assert t["warn_low"] is None and t["crit_low"] is None
    assert t["crit_high"] == pytest.approx(57.5)


def test_compute_from_frame() -> None:
    df = pd.DataFrame({"volt": [1.0, 3.0], "rotate": [1.0, 3.0], "pressure": [1.0, 3.0],
                       "vibration": [1.0, 3.0]})  # fmt: skip
    t = compute_thresholds(df)
    assert set(t) == {"volt", "rotate", "pressure", "vibration"}
    assert t["volt"]["mean"] == pytest.approx(2.0)
