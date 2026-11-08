"""Unit tests for the adapted screening model (pure computation, no I/O)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.screening import compute_features, pos_sat, sat, score100, sigmoid  # noqa: E402
from backend import sample_data  # noqa: E402


def test_sat_bounds_and_sign():
    assert sat(0.0) == 0.0
    assert 0.0 < sat(1.5) < 1.0
    assert -1.0 < sat(-1.5) < 0.0
    assert pos_sat(-0.5) == 0.0
    assert 0.0 < pos_sat(0.5) < 1.0


def test_score100_monotonic_and_bounded():
    prev = -1.0
    for raw in [0.0, 0.1, 0.5, 1.0, 2.0, 5.0]:
        v = score100(raw, 0.45)
        assert 0.0 <= v < 100.0
        assert v >= prev
        prev = v


def test_sigmoid_bounds():
    assert 0.0 < sigmoid(-100.0) < 0.5 < sigmoid(100.0) < 1.0


def _summary(result):
    return result["verdict"], result["scores"]


def test_growth_regime_produces_positive_organic_growth():
    result = compute_features(sample_data.seed_klines("SOLUSDT"))
    assert result["ok"]
    assert result["features"]["gOrganic"] > 0.0
    assert result["features"]["cashConversion"] > 0.5


def test_froth_regime_flagged_low_quality():
    result = compute_features(sample_data.seed_klines("DOGEUSDT"))
    assert result["ok"]
    assert result["features"]["gRev"] > 0.0
    assert result["features"]["participationGrowth"] < 0.0
    assert result["filters"]["lowQualityGrowth"] is True
    assert result["verdict"] == "AVOID"


def test_distortion_regime_is_not_long():
    result = compute_features(sample_data.seed_klines("BNBUSDT"))
    assert result["ok"]
    # price-driven USD volume with flat unit volume must not produce LONG
    assert result["verdict"] in ("NO_SIGNAL", "NEUTRAL")
    assert result["filters"]["billingDistortion"] or abs(result["features"]["gOrganic"]) <= 0.05


def test_unstable_regime_trips_red_flag_or_avoid():
    result = compute_features(sample_data.seed_klines("WIFUSDT"))
    assert result["ok"]
    assert result["risk"]["abnormalEvents30d"] >= 1
    assert result["verdict"] in ("AVOID", "NEUTRAL", "WATCH")


def test_insufficient_history_handled():
    result = compute_features(sample_data.seed_klines("BTCUSDT")[:40])
    assert result["ok"] is False
    assert result["verdict"] == "INSUFFICIENT_DATA"


def test_scores_are_finite_and_bounded():
    for symbol in ["BTCUSDT", "ETHUSDT", "LINKUSDT", "INJUSDT", "ATOMUSDT"]:
        result = compute_features(sample_data.seed_klines(symbol))
        assert result["ok"]
        for value in result["scores"].values():
            assert 0.0 <= value <= 100.0 or 0.0 <= value <= 1.0


def test_seed_data_deterministic():
    a = sample_data.seed_klines("ETHUSDT")
    b = sample_data.seed_klines("ETHUSDT")
    assert a == b
