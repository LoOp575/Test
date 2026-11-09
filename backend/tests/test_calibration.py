"""Calibration-toolkit tests — all offline/synthetic; no network, no real data.

The synthetic checks prove: labels are computed correctly, time-ordered split
respects chronology, the logistic fit recovers the planted direction of
synthetic signal, and metrics behave (AUC/Brier/logloss bounds). They do NOT
guarantee production numbers — those require a real run of scripts/calibrate.py.
"""
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import calibrate  # noqa: E402
from backend.screening import pos_sat  # noqa: E402


def _mk_candles(closes, volumes, trades=None, taker_ratio=0.55):
    out = []
    t = 1_600_000_000_000
    for i, (c, v) in enumerate(zip(closes, volumes)):
        out.append({
            "date": t + i * 86_400_000,
            "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
            "volume": v, "quoteVolume": v * c,
            "trades": (trades or [1000 + i]) and (trades[i] if trades else 1000 + i),
            "takerBuyBase": v * taker_ratio,
            "takerBuyQuote": v * c * taker_ratio,
        })
    return out


def test_forward_organic_growth_label():
    volumes = [100.0] * 90 + [400.0] * 31  # sharp expansion AFTER the trailing 30d
    closes = [100.0] * len(volumes)
    candles = _mk_candles(closes, volumes)
    anchor = 89  # last flat-volume index
    fwd = calibrate.forward_organic_growth(candles, anchor, window=30)
    # forward 30d window = candles[90:120] = 400; trailing = candles[60:90] = 100
    assert fwd == pytest.approx(3.0, rel=1e-9)
    assert calibrate.forward_organic_growth(candles, 200, 30) is None


def test_forward_organic_growth_negative():
    volumes = [400.0] * 90 + [100.0] * 31
    closes = [100.0] * len(volumes)
    out = calibrate.forward_organic_growth(_mk_candles(closes, volumes), 89, 30)
    assert out == pytest.approx(-0.75, rel=1e-9)


def test_time_split_is_chronological():
    assert calibrate.time_split(100, 0.75) == 75
    assert calibrate.time_split(3, 0.75) == 2


def test_logistic_fit_recovers_planted_signal():
    import random
    random.seed(7)
    X, y = [], []
    for _ in range(400):
        x = [random.gauss(0, 1) for _ in range(6)]
        z = -0.5 + 1.8 * x[0] + 0.9 * x[3]
        p = 1.0 / (1.0 + math.exp(-z))
        y.append(1 if random.random() < p else 0)
        X.append([1.0] + x)
    w = calibrate.fit_logistic(X, y, l2=0.1)
    assert w[1] > 0.5  # dominant feature pushed positive
    p = calibrate.predict_proba(X, w)
    assert calibrate.auc(p, y) > 0.75
    assert 0.0 < calibrate.brier(p, y) < 0.35


def test_auc_perfect_separation():
    assert calibrate.auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == pytest.approx(1.0)


def test_logloss_and_brier_bounds():
    y = [0, 0, 1, 1]
    assert calibrate.logloss([0.5, 0.5, 0.5, 0.5], y) == pytest.approx(math.log(2), rel=1e-6)
    assert calibrate.brier([1.0, 0.0, 1.0, 0.0], y) == pytest.approx(0.5)
    assert calibrate.brier([0.0, 1.0], [1, 0]) == pytest.approx(1.0)


def test_run_end_to_end_on_synthetic_universe(tmp_path):
    import random
    random.seed(42)

    def coin(drift_vol):
        vol, close = 1000.0, 100.0
        closes, volumes = [], []
        for i in range(240):
            vol *= 1.0 + drift_vol + random.gauss(0, 0.02)
            close *= 1.0 + random.gauss(0, 0.02)
            volumes.append(vol)
            closes.append(close)
        return _mk_candles(closes, volumes)

    universe = {f"COIN{i}USDT": coin(drift) for i, drift in enumerate([0.004, 0.002, 0.0, -0.002, -0.004, 0.001])}
    report = calibrate.run(universe, step_days=7, min_samples=40)
    out = tmp_path / "calibration_report.json"
    out.write_text(__import__("json").dumps(report, indent=2))
    assert report["samples"] >= 50
    assert 0.0 <= report["validation"]["brier"] <= 1.0
    assert 0.5 <= report["validation"]["auc"] <= 1.0
    assert report["coefficients"]["intercept"] == report["coefficients"]["intercept"]  # finite
    assert len(report["validation"]["reliability"]) == 10
    for bucket in report["validation"]["reliability"]:
        assert bucket["count"] >= 0


def test_feature_vector_matches_screening():
    volumes = [100.0 + i for i in range(150)]
    closes = [100.0 * (1.001 ** i) for i in range(150)]
    candles = _mk_candles(closes, volumes, taker_ratio=0.56)
    res = calibrate.compute_features(candles)
    vec = calibrate.feature_vector(res)
    assert vec is not None
    assert vec[0] == pytest.approx(pos_sat(res["features"]["gOrganic"]))
    assert len(vec) == 6
