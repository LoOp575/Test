"""Independent stochastic-analysis engines for Meridian's standalone menu pages.

No imports from the screener, calibration, Fokker-Planck, or NEURON layers.
All random simulations are seeded for reproducibility.
"""
from __future__ import annotations

import math
import random
from statistics import mean, median
from typing import Iterable


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def random_walk(*, start_price: float, sigma: float, periods: int, paths: int, seed: int) -> dict:
    if start_price <= 0 or sigma <= 0 or periods < 1 or paths < 100:
        raise ValueError("Input di luar batas.")
    rng = random.Random(seed)
    finals: list[float] = []
    sample_paths: list[list[float]] = []
    for i in range(paths):
        x = start_price
        path = [x]
        for _ in range(periods):
            x += rng.gauss(0.0, sigma * start_price)
            path.append(x)
        finals.append(x)
        if i < 12:
            sample_paths.append([round(v, 8) for v in path])
    return {
        "ok": True, "engine": "random-walk", "model": "X[t] = X[t-1] + epsilon[t]",
        "assumptions": {"incrementMean": 0, "incrementStdDev": sigma * start_price,
                        "independentGaussianIncrements": True, "seed": seed},
        "summary": {
            "paths": paths, "periods": periods, "startPrice": start_price,
            "meanFinal": mean(finals), "medianFinal": median(finals),
            "stdFinal": math.sqrt(sum((x - mean(finals)) ** 2 for x in finals) / len(finals)),
            "p05": _quantile(finals, 0.05), "p50": _quantile(finals, 0.50),
            "p95": _quantile(finals, 0.95),
            "probabilityAboveStart": sum(x > start_price for x in finals) / paths,
            "theoreticalVariance": periods * (sigma * start_price) ** 2,
        },
        "samplePaths": sample_paths,
        "warning": "Model diskret Gaussian tanpa drift; bukan ramalan harga dan tidak memodelkan fat tails atau regime shift."
    }


def brownian_motion(*, horizon: float, sigma: float, steps: int, paths: int, seed: int) -> dict:
    if horizon <= 0 or sigma <= 0 or steps < 2 or paths < 100:
        raise ValueError("Input di luar batas.")
    rng = random.Random(seed)
    dt = horizon / steps
    finals: list[float] = []
    sample_paths: list[list[float]] = []
    for i in range(paths):
        w = 0.0
        path = [0.0]
        for _ in range(steps):
            w += sigma * math.sqrt(dt) * rng.gauss(0.0, 1.0)
            path.append(w)
        finals.append(w)
        if i < 12:
            sample_paths.append([round(v, 8) for v in path])
    empirical_var = sum((x - mean(finals)) ** 2 for x in finals) / len(finals)
    return {
        "ok": True, "engine": "brownian-motion", "model": "dW = sigma * sqrt(dt) * Z",
        "assumptions": {"zeroStart": True, "independentNormalIncrements": True, "seed": seed},
        "summary": {
            "paths": paths, "steps": steps, "horizon": horizon, "dt": dt,
            "meanFinal": mean(finals), "varianceFinal": empirical_var,
            "theoreticalVariance": sigma * sigma * horizon,
            "stdFinal": math.sqrt(empirical_var),
            "theoreticalStdDev": sigma * math.sqrt(horizon),
            "p05": _quantile(finals, 0.05), "p50": _quantile(finals, 0.50),
            "p95": _quantile(finals, 0.95),
        },
        "samplePaths": sample_paths,
        "warning": "Simulasi Brownian terdiskretisasi; lintasan kontinu hanya merupakan limit matematis, bukan hasil diskret yang identik."
    }


def detect_patterns(returns: Iterable[float], *, lag: int = 4) -> dict:
    xs = [float(x) for x in returns]
    if len(xs) < 20 or lag < 2 or lag >= len(xs) // 2:
        raise ValueError("Butuh minimal 20 return dan lag kurang dari setengah panjang data.")
    if any(not math.isfinite(x) for x in xs):
        raise ValueError("Semua return harus berupa angka finite.")
    mu = mean(xs)
    centered = [x - mu for x in xs]
    var = sum(x * x for x in centered) / len(centered)
    if var <= 1e-18:
        raise ValueError("Variansi return nol; pola tidak dapat diuji.")
    denom = sum(x * x for x in centered)
    acf1 = sum(centered[i] * centered[i - 1] for i in range(1, len(centered))) / denom
    n = len(xs)
    q_sums = [sum(centered[i:i + lag]) for i in range(n - lag + 1)]
    q_var = sum(x * x for x in q_sums) / len(q_sums)
    vr = q_var / (lag * var)
    # Approximate standard-error band for lag-1 autocorrelation under white noise.
    threshold = 1.96 / math.sqrt(n)
    if acf1 > threshold and vr > 1.0 + threshold:
        verdict = "PERSISTENCE_EVIDENCE"
        note = "Ada indikasi dependensi positif/persistensi; belum membuktikan edge prediktif."
    elif acf1 < -threshold and vr < 1.0 - threshold:
        verdict = "MEAN_REVERSION_EVIDENCE"
        note = "Ada indikasi pembalikan jangka pendek; belum membuktikan edge prediktif."
    else:
        verdict = "NO_CLEAR_PATTERN"
        note = "Belum ada bukti sederhana yang cukup untuk menolak perilaku mirip white-noise."
    return {
        "ok": True, "engine": "stochastic-pattern-detector",
        "summary": {"observations": n, "meanReturn": mu, "variance": var,
                    "lag1Autocorrelation": acf1, "varianceRatio": vr,
                    "lag": lag, "whiteNoiseApproxThreshold95": threshold,
                    "verdict": verdict, "interpretation": note},
        "limitations": [
            "Ambang autokorelasi adalah pendekatan, bukan uji formal lengkap.",
            "Variance ratio di sini diagnostik sederhana, bukan Lo-MacKinlay heteroskedasticity-robust test.",
            "Gunakan data out-of-sample dan koreksi multiple testing sebelum menyimpulkan pola."
        ]
    }
