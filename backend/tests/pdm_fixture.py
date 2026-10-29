"""Writes a small, schema-compatible PdM dataset so tests never need the Kaggle download."""

import random
from pathlib import Path

import pandas as pd


def write_pdm_fixture(
    out: Path, n_machines: int = 100, hours: int = 48, seed: int = 7
) -> dict[str, pd.DataFrame]:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    ids = list(range(1, n_machines + 1))

    machines = pd.DataFrame(
        {
            "machineID": ids,
            "model": [f"model{1 + (i % 4)}" for i in ids],
            "age": [rng.randint(0, 20) for _ in ids],
        }
    )
    stamps = pd.date_range("2015-01-01 06:00:00", periods=hours, freq="h")
    telemetry = pd.DataFrame(
        [
            {
                "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "machineID": m,
                "volt": rng.gauss(170, 15),
                "rotate": rng.gauss(446, 52),
                "pressure": rng.gauss(100, 11),
                "vibration": rng.gauss(40, 5),
            }
            for m in ids
            for ts in stamps
        ]
    )

    errors = pd.DataFrame(
        [
            {
                "datetime": stamps[rng.randrange(hours)].strftime("%Y-%m-%d %H:%M:%S"),
                "machineID": rng.choice(ids),
                "errorID": f"error{rng.randint(1, 5)}",
            }
            for _ in range(60)
        ]
    )
    failures = pd.DataFrame(
        [
            {
                "datetime": stamps[rng.randrange(hours)].strftime("%Y-%m-%d %H:%M:%S"),
                "machineID": m,
                "failure": f"comp{rng.randint(1, 4)}",
            }
            for m in rng.sample(ids, 12)
        ]
    )
    scheduled = pd.DataFrame(
        [
            {
                "datetime": "2014-12-15 06:00:00",
                "machineID": rng.choice(ids),
                "comp": f"comp{rng.randint(1, 4)}",
            }
            for _ in range(30)
        ]
    )
    # every failure is also a maintenance replacement (as in the real dataset)
    maint = pd.concat([scheduled, failures.rename(columns={"failure": "comp"})], ignore_index=True)

    frames = {
        "PdM_machines.csv": machines,
        "PdM_telemetry.csv": telemetry,
        "PdM_errors.csv": errors,
        "PdM_maint.csv": maint,
        "PdM_failures.csv": failures,
    }
    for name, df in frames.items():
        df.to_csv(out / name, index=False)
    return frames
