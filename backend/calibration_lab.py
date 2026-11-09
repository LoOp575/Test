"""
Meridian — Calibration Lab (server-side, on-demand).

Walk-forward calibration of the p^(accel) sigmoid against **live public
Binance daily klines** (never the synthetic seed universe, never cached
snapshots dressed up as live data). The fitted L2-logistic model is compared
against the hand-tuned production heuristic and returned as a review-ready
report with an explicit pass/fail verdict.

Hard rules:
  * Nothing here mutates production constants. Applying a fitted formula is a
    deliberate, reviewed code change made by a human, behind tests.
  * Data comes only from data-api.binance.vision / api.binance.us.
  * Requests fail loudly-but-gracefully: symbols that cannot be fetched are
    reported under `skipped`; the lab never fabricates candles to fill gaps.
"""

from __future__ import annotations

import asyncio
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from backend import market_data
from backend.market_data import _parse_klines
from backend.screening import P_ACC_WEIGHTS
from scripts import calibrate as calib

# Binance klines hard limit is 1000 rows; 730d (~2y) default keeps the Vercel
# 60s budget comfortable.
MIN_KLINES = 61
MAX_KLINES = 1000
DEFAULT_LOOKBACK = 730
MAX_SYMBOLS = 40
FETCH_CONCURRENCY = 10

KLINE_HOSTS = [
    ("binance-vision", "https://data-api.binance.vision/api/v3/klines"),
    ("binance-us", "https://api.binance.us/api/v3/klines"),
]


def formula_meta() -> dict[str, Any]:
    """Metadata for the `Pendekatan rumus saat ini` panel."""
    return {
        "name": "p^(accel) — heuristic demand-acceleration sigmoid",
        "form": "z = b0 + b_g·g_pos + b_a·a_pos + b_d·d_pos + b_gap·max(0, gap_pos) + b_c·(c_cash − 0.5) + b_s·(s_flow − 0.7); p = sigmoid(z)",
        "weights": dict(P_ACC_WEIGHTS),
        "status": "hand-tuned heuristic — belum tervalidasi walk-forward (lihat tab Kalibrasi)",
        "features": [
            {"key": "g_pos", "meaning": "organic (unit-volume) growth, saturating", "saturates": "(1+e^-x)^-1"},
            {"key": "a_pos", "meaning": "acceleration of organic growth vs prior window", "saturates": "(1+e^-x)^-1"},
            {"key": "d_pos", "meaning": "organic lead vs price appreciation", "saturates": "(1+e^-x)^-1"},
            {"key": "gap_pos", "meaning": "organic lead relative to 1 + price growth (under-recognition gap)", "saturates": "sat(·)"},
            {"key": "c_cash", "meaning": "buy-side share of base volume (taker-buy)", "saturates": "raw, centred at 0.5"},
            {"key": "s_flow", "meaning": "participation stability (trade-count churn), exp(-λ·steepening decline)", "saturates": "raw, centred at 0.7"},
        ],
        "sigmoid": "sigmoid(z) = 1 / (1 + e^-z)",
        "thresholds": "LONG ≥ 0.62 (heuristic), see screening.py — juga belum dikalibrasi",
    }


async def _fetch_klines(client: httpx.AsyncClient, symbol: str, limit: int):
    debug: list[str] = []
    for name, base in KLINE_HOSTS:
        try:
            url = f"{base}?symbol={symbol}&interval=1d&limit={limit}"
            raw = await market_data._fetch_json(client, url, timeout=10.0)
            candles = _parse_klines(raw)
            if len(candles) >= MIN_KLINES:
                return name, candles, debug
            debug.append(f"{name}: only {len(candles)} candles")
        except Exception as exc:  # noqa: BLE001 - report, never fabricate
            debug.append(f"{name}: {type(exc).__name__}: {exc}")
    return None, None, debug


async def load_universe(
    client: httpx.AsyncClient,
    lookback_days: int,
    symbols: Optional[list[str]] = None,
) -> tuple[dict[str, list[dict]], list[dict], dict[str, str], int]:
    """Fetch live klines for the lab universe. Returns (candles, skipped, sources, limit)."""
    limit = max(MIN_KLINES, min(int(lookback_days), MAX_KLINES))
    syms = [s.upper().strip() for s in (symbols or calib.DEFAULT_SYMBOLS) if s.strip()][:MAX_SYMBOLS]

    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    candles_by_symbol: dict[str, list[dict]] = {}
    sources: dict[str, str] = {}
    skipped: list[dict] = []

    async def one(sym: str) -> None:
        async with sem:
            name, klines, debug = await _fetch_klines(client, sym, limit)
            if klines:
                candles_by_symbol[sym] = klines
                sources[sym] = name or "unknown"
            else:
                skipped.append({"symbol": sym, "reason": "; ".join(debug)[:300]})

    await asyncio.gather(*(one(s) for s in syms))
    return candles_by_symbol, skipped, sources, limit


def _fmt_day(ms: Optional[int]) -> Optional[str]:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _verdict(rep: dict[str, Any]) -> dict[str, Any]:
    """Explicit pass/fail vs production heuristic. `lulus` requires ALL criteria."""
    fitted = rep["validation"]["brier"]
    heur = rep["currentHeuristic"]["validationBrier"]
    auc_fit = rep["validation"]["auc"]
    auc_heu = rep["currentHeuristic"]["validationAuc"]

    def _finite(v):
        return isinstance(v, (int, float)) and math.isfinite(v)
    p06 = rep["validation"]["p_ge_0.6"]
    base = float(rep["baseRateValidation"])

    prec = p06.get("precision")
    fires = int(p06.get("count") or 0)
    criteria = {
        "brierImproves": {
            "ok": fitted < heur - 1e-4,
            "detail": f"fitted Brier {fitted:.4f} vs heuristic {heur:.4f} (harus lebih rendah)",
        },
        "aucNotWorse": {
            "ok": _finite(auc_fit) and _finite(auc_heu) and auc_fit >= auc_heu - 0.01,
            "detail": (
                f"fitted AUC {auc_fit:.3f} vs heuristic {auc_heu:.3f} (toleransi -0.01)"
                if _finite(auc_fit)
                else f"AUC tidak terdefinisi (validasi satu kelas) — fitur gagal memisahkan kelas"
            ),
        },
        "precisionAtPoint6BeatsBaseRate": {
            "ok": (prec is not None and prec >= base and fires >= 3),
            "detail": (
                f"precision@p≥0.6 {prec:.3f} (n={fires}) vs base rate {base:.3f} (butuh n≥3)"
                if prec is not None
                else f"model tidak pernah menembak p≥0.6 (base rate {base:.3f})"
            ),
        },
    }
    passed = all(c["ok"] for c in criteria.values())
    reasons = [c["detail"] for c in criteria.values() if c["ok"]]
    failures = [c["detail"] for c in criteria.values() if not c["ok"]]
    notes: list[str] = []
    if rep["samples"] < 150:
        notes.append(
            f"Sampel kecil ({rep['samples']} baris) — metrik validasi bising; perbesar lookback/universe sebelum mengambil keputusan."
        )
    if p06.get("count", 0) < 3:
        notes.append("Model hampir tidak menembak p≥0.6 — cek reliability table sebelum memakai threshold produksi.")
    return {
        "status": "lulus" if passed else "kurang",
        "criteria": criteria,
        "passed": reasons,
        "failed": failures,
        "notes": notes,
    }


def _suggested_block(rep: dict[str, Any]) -> str:
    c, fm, fs = rep["coefficients"], rep["featureMean"], rep["featureStd"]
    keys = ["g_pos", "a_pos", "d_pos", "gap_pos", "c_cash", "s_flow"]
    lines = [
        "# Saran p^acc (BELUM DITERAPKAN) — koefisien di skala TERSTANDARDISASI",
        "# x' = (x - mean) / std ; p = sigmoid(z)",
        f"z = {c['intercept']:.6f}",
    ]
    for k in keys:
        lines.append(f"  + {c[k]:+.6f} * ({k} - ({fm[k]:.6f})) / {fs[k]:.6f}")
    lines.append("# Porting yang benar: pakai featureMean/featureStd ini di screening.py,")
    lines.append("# atau refit raw (scripts/calibrate.py) dan review Brier/AUC/reliability dulu.")
    return "\n".join(lines)


def _sanitize(obj):
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _decorate(
    rep: dict[str, Any],
    *,
    skipped: list[dict],
    sources: dict[str, str],
    candles_by_symbol: dict[str, list[dict]],
    params: dict[str, Any],
) -> dict[str, Any]:
    per_symbol = Counter({sym: len(v) for sym, v in candles_by_symbol.items()})
    rep = _sanitize(dict(rep))
    rep.update(
        {
            "ok": True,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "params": params,
            "universe": sorted(candles_by_symbol),
            "universeSize": len(candles_by_symbol),
            "skipped": skipped,
            "sources": sources,
            "perSymbolCandles": dict(sorted(per_symbol.items())),
            "anchorFirst": _fmt_day(rep.get("anchorFirstMs")),
            "anchorLast": _fmt_day(rep.get("anchorLastMs")),
            "verdict": _verdict(rep),
            "suggestedBlock": _suggested_block(rep),
            "warning": (
                "Kalibrasi dijalankan on-demand di server ini. Tidak ada konstanta production "
                "yang berubah otomatis — penerapan formula = perubahan kode yang direview manusia."
            ),
        }
    )
    return rep


async def run_calibration(
    client: httpx.AsyncClient,
    *,
    lookback_days: int = DEFAULT_LOOKBACK,
    step_days: int = 7,
    label: str = "pos",
    symbols: Optional[list[str]] = None,
    min_samples: int = 60,
    _klines: Optional[tuple[dict[str, list[dict]], list[dict], dict[str, str], int]] = None,
) -> dict[str, Any]:
    """Fetch live klines → walk-forward fit → report with verdict. Never raises."""
    if _klines is not None:
        candles_by_symbol, skipped, sources, limit = _klines
    else:
        candles_by_symbol, skipped, sources, limit = await load_universe(client, lookback_days, symbols)

    params = {
        "lookbackDays": lookback_days,
        "klinesLimit": limit,
        "stepDays": step_days,
        "label": label,
    }
    if not candles_by_symbol:
        return {
            "ok": False,
            "reason": "Semua fetch klines gagal — data-api.binance.vision & api.binance.us tidak terjangkau. Coba lagi nanti.",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "params": params,
            "skipped": skipped,
        }
    try:
        rep = calib.run(candles_by_symbol, step_days=step_days, min_samples=min_samples, label=label)
    except Exception as exc:  # noqa: BLE001 - fail loudly, never fabricate
        return {
            "ok": False,
            "reason": str(exc),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "params": params,
            "skipped": skipped,
            "universe": sorted(candles_by_symbol),
            "perSymbolCandles": {k: len(v) for k, v in sorted(candles_by_symbol.items())},
        }
    return _decorate(
        rep,
        skipped=skipped,
        sources=sources,
        candles_by_symbol=candles_by_symbol,
        params=params,
    )


async def sweep_calibration(
    client: httpx.AsyncClient,
    *,
    lookback_days: int = DEFAULT_LOOKBACK,
    step_days: int = 7,
    symbols: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Auto-sweep: fit both label variants on ONE fetch, rank by validation log-loss.

    Powers the 'Cari rumus terbaik' button. Bounded to keep the 60s serverless
    budget: fetch once, fit twice (each ~seconds), no hyper-parameter search
    beyond the built-in L2 grid.
    """
    klines = await load_universe(client, lookback_days, symbols)
    results = []
    for label in ("pos", "strong"):
        rep = await run_calibration(
            client,
            lookback_days=lookback_days,
            step_days=step_days,
            label=label,
            min_samples=40,
            _klines=klines,
        )
        if rep.get("ok"):
            results.append(
                {
                    "label": label,
                    "l2": rep["l2"],
                    "samples": rep["samples"],
                    "validation": rep["validation"],
                    "train": rep["train"],
                    "currentHeuristic": rep["currentHeuristic"],
                    "verdict": rep["verdict"],
                }
            )
        else:
            results.append({"label": label, "ok": False, "reason": rep.get("reason")})
    valid = [r for r in results if r.get("l2") is not None]
    best = min(valid, key=lambda r: r["validation"]["logloss"]) if valid else None
    candles_by_symbol, skipped, sources, limit = klines
    return {
        "ok": bool(valid),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "params": {"lookbackDays": lookback_days, "klinesLimit": limit, "stepDays": step_days},
        "universe": sorted(candles_by_symbol),
        "skipped": skipped,
        "candidates": results,
        "bestLabel": best["label"] if best else None,
        "bestValidationLogloss": best["validation"]["logloss"] if best else None,
        "verdict": best["verdict"] if best else None,
        "warning": "Sweep otomatis — pemenang dipilih by validation log-loss, bukan oleh opini. Tetap perlu review sebelum dipakai.",
    }
