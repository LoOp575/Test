import pytest

from backend.crypto_intelligence_v2 import DataQualityError, compute_v2_features


def make_candles(n=120, *, base_volume_fn=None, price_fn=None, buy_share=0.6):
    rows = []
    for i in range(n):
        close = price_fn(i) if price_fn else 10.0
        volume = base_volume_fn(i) if base_volume_fn else 100.0
        rows.append({
            "date": i * 86_400_000,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": volume,
            "quoteVolume": volume * close,
            "trades": 100.0,
            "takerBuyBase": volume * buy_share,
        })
    return rows


def test_growth_and_effective_price_decomposition():
    candles = make_candles(
        90,
        base_volume_fn=lambda i: 100.0 if i < 60 else 150.0,
        price_fn=lambda i: 10.0 if i < 60 else 12.0,
    )
    features = compute_v2_features(candles)["features"]
    assert features["baseVolumeGrowth30"] == pytest.approx(0.5)
    assert features["quoteVolumeGrowth30"] == pytest.approx(0.8)
    assert features["effectivePriceGrowth30"] == pytest.approx(0.2)


def test_buy_imbalance_is_centered_on_zero():
    buyer = compute_v2_features(make_candles(90, buy_share=0.7))["features"]
    balanced = compute_v2_features(make_candles(90, buy_share=0.5))["features"]
    seller = compute_v2_features(make_candles(90, buy_share=0.3))["features"]
    assert buyer["buyImbalance30"] == pytest.approx(0.4)
    assert balanced["buyImbalance30"] == pytest.approx(0.0)
    assert seller["buyImbalance30"] == pytest.approx(-0.4)


def test_partial_history_does_not_invent_acceleration():
    result = compute_v2_features(make_candles(75))
    assert result["dataQuality"]["history"] == "partial"
    assert result["features"]["baseVolumeAcceleration"] is None
    assert result["features"]["baseVolumeGrowth30"] == pytest.approx(0.0)


def test_full_history_computes_acceleration():
    candles = make_candles(
        90,
        base_volume_fn=lambda i: 100.0 if i < 30 else (120.0 if i < 60 else 160.0),
    )
    features = compute_v2_features(candles)["features"]
    assert features["baseVolumeGrowthPrevious30"] == pytest.approx(0.2)
    assert features["baseVolumeAcceleration"] == pytest.approx(1 / 3 - 0.2)


def test_buy_pressure_persistence_is_bounded():
    candles = make_candles(90)
    for i in range(60, 90):
        candles[i]["takerBuyBase"] = 40.0
    persistence = compute_v2_features(candles)["features"]["buyPressurePersistence30"]
    assert persistence == pytest.approx(0.0)


def test_rejects_non_chronological_candles():
    candles = make_candles(61)
    candles[10]["date"] = candles[9]["date"]
    with pytest.raises(DataQualityError, match="chronological"):
        compute_v2_features(candles)


def test_rejects_taker_buy_above_total_volume():
    candles = make_candles(61)
    candles[-1]["takerBuyBase"] = candles[-1]["volume"] + 1
    with pytest.raises(DataQualityError, match="taker_buy"):
        compute_v2_features(candles)


def test_rejects_nan_and_negative_volume():
    candles = make_candles(61)
    candles[-1]["quoteVolume"] = float("nan")
    with pytest.raises(DataQualityError, match="non_finite"):
        compute_v2_features(candles)
    candles = make_candles(61)
    candles[-1]["volume"] = -1
    with pytest.raises(DataQualityError, match="negative_activity"):
        compute_v2_features(candles)


def test_flags_calendar_gaps_instead_of_hiding_them():
    candles = make_candles(61)
    candles[30]["date"] += 86_400_000
    for i in range(31, len(candles)):
        candles[i]["date"] += 86_400_000
    result = compute_v2_features(candles)
    assert "calendar_gaps_present_windows_are_observation_based" in result["dataQuality"]["warnings"]


def test_volume_shocks_require_sufficient_baseline():
    short = compute_v2_features(make_candles(90))["features"]
    long = compute_v2_features(make_candles(120))["features"]
    assert short["volumeShockDays30"] is None
    assert long["volumeShockDays30"] == 0



def test_calendar_gap_withholds_daily_volatility():
    candles = make_candles(120)
    candles[70]["date"] += 86_400_000
    for i in range(71, len(candles)):
        candles[i]["date"] += 86_400_000
    features = compute_v2_features(candles)["features"]
    assert features["volatility90"] is None
    assert features["abnormalReturnDays30"] is None


def test_rejects_positive_base_volume_without_quote_volume():
    candles = make_candles(61)
    candles[-1]["quoteVolume"] = 0.0
    with pytest.raises(DataQualityError, match="missing_quote_volume"):
        compute_v2_features(candles)
