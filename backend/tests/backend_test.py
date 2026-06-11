"""LQ-Short Hunter backend tests."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://6fbbe2d4-5d72-4d80-b665-a11b528745ef.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# --- /api/health ---
def test_health(session):
    r = session.get(f"{API}/health", timeout=30)
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True
    assert j.get("version") == "2.0.0"


# --- /api/markets ---
def test_markets_list(session):
    r = session.get(f"{API}/markets", timeout=30)
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True
    assert j.get("source") in ("binance-spot", "binance-futures", "coingecko", "local-seed")
    data = j.get("data")
    assert isinstance(data, list) and len(data) > 0
    row = data[0]
    for k in ("symbol", "lastPrice", "priceChangePercent", "quoteVolume", "highPrice", "lowPrice", "volatility24h"):
        assert k in row, f"missing field {k} in market row"


# --- /api/analyze/{symbol} ---
def test_analyze_btc(session):
    r = session.post(f"{API}/analyze/BTCUSDT", timeout=60)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert "market" in j and "autoLevels" in j and "results" in j
    auto = j["autoLevels"]
    last = j["market"]["lastPrice"]
    assert auto["takeProfit"] < last, "TP must be below entry for short"
    assert auto["stopLoss"] > last, "SL must be above entry for short"
    assert "exhaustion" in auto
    assert "phase" in auto["exhaustion"]
    assert "exhaustionScore" in auto["exhaustion"]
    res = j["results"]
    assert "score" in res and "probabilities" in res and "trade" in res and "stats" in res
    assert isinstance(res["chart"]["buckets"], list) and len(res["chart"]["buckets"]) > 0


def test_analyze_pepe(session):
    r = session.post(f"{API}/analyze/PEPEUSDT", timeout=60)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert j["market"]["lastPrice"] > 0
    # ensure no NaN/Inf in serialized output (json wouldn't allow it but check structure)
    assert isinstance(j["results"]["score"]["scorePercent"], (int, float))


def test_analyze_bad_symbol(session):
    r = session.post(f"{API}/analyze/BADSYMBOLUSDT", timeout=30)
    assert r.status_code == 404


# --- /api/simulate ---
def test_simulate_valid(session):
    body = {
        "currentPrice": 100, "takeProfit": 95, "stopLoss": 103,
        "annualVolatility": 0.6, "daysForecast": 7, "simulations": 10000,
    }
    r = session.post(f"{API}/simulate", json=body, timeout=30)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert "score" in j and "probabilities" in j


def test_simulate_invalid_short(session):
    body = {
        "currentPrice": 100, "takeProfit": 105, "stopLoss": 110,
        "annualVolatility": 0.6, "daysForecast": 7, "simulations": 5000,
    }
    r = session.post(f"{API}/simulate", json=body, timeout=30)
    # repair logic may auto-fix tp - check either 400 with msg, or test that repair occurred
    assert r.status_code in (400,), f"expected 400 for invalid short setup, got {r.status_code}: {r.text}"


# --- /api/agent-analysis ---
def test_agent_analysis(session):
    # First get a real payload
    a = session.post(f"{API}/analyze/BTCUSDT", timeout=60).json()
    body = {"market": a["market"], "autoLevels": a["autoLevels"], "results": a["results"]}
    r = session.post(f"{API}/agent-analysis", json=body, timeout=90)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert isinstance(j.get("analysis"), str) and len(j["analysis"]) > 50
    assert j.get("source") in ("aixchia", "emergent-llm", "rule-based")
