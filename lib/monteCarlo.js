/* ============================================================
   Monte Carlo Simulation Engine (GBM — Geometric Brownian Motion)

   All calculation logic lives here, completely decoupled
   from the UI so it can be reused or tested independently.

   NOTE:
   This model is probabilistic, not a guaranteed trading signal.
   ============================================================ */

import { normalRandom, clamp, toNumber } from './utils';

export function calcLiquidationMagnet(shortLiqAbove, longLiqBelow) {
  const shortAbove = Math.max(0, toNumber(shortLiqAbove, 0));
  const longBelow = Math.max(0, toNumber(longLiqBelow, 0));
  const sum = shortAbove + longBelow;

  if (sum === 0) return 0;

  return (shortAbove - longBelow) / sum;
}

export function calcLiquidityPressure(spotFlow, oiFlow, liquidationMagnet) {
  const spot = clamp(toNumber(spotFlow, 0), -1, 1);
  const oi = clamp(toNumber(oiFlow, 0), -1, 1);
  const liq = clamp(toNumber(liquidationMagnet, 0), -1, 1);

  return 0.4 * spot + 0.3 * oi + 0.3 * liq;
}

function repairShortLevels(currentPrice, rawTakeProfit, rawStopLoss) {
  const price = Math.max(1e-12, toNumber(currentPrice, 65000));
  let takeProfit = toNumber(rawTakeProfit, price * 0.96);
  let stopLoss = toNumber(rawStopLoss, price * 1.03);

  // Hard repair: no caller is allowed to break short setup levels.
  // This protects small tokens, scientific notation, stale state, and manual bad inputs.
  if (!Number.isFinite(takeProfit) || takeProfit <= 0 || takeProfit >= price) {
    takeProfit = price * 0.96;
  }

  if (!Number.isFinite(stopLoss) || stopLoss <= price) {
    stopLoss = price * 1.03;
  }

  // Final safety clamp.
  takeProfit = Math.max(price * 0.000001, Math.min(takeProfit, price * 0.999));
  stopLoss = Math.max(stopLoss, price * 1.001);

  return {
    currentPrice: price,
    takeProfit,
    stopLoss
  };
}

export function normalizeSimulationInput(raw = {}) {
  const basePrice = Math.max(1e-12, toNumber(raw.currentPrice, 65000));
  const levels = repairShortLevels(basePrice, raw.takeProfit, raw.stopLoss);

  const simulations = Math.round(
    clamp(toNumber(raw.simulations, 50000), 1000, 100000)
  );

  const annualVolatility = clamp(
    toNumber(raw.annualVolatility, 0.65),
    0.01,
    5
  );

  const daysForecast = clamp(
    toNumber(raw.daysForecast, 7),
    1,
    365
  );

  return {
    currentPrice: levels.currentPrice,
    takeProfit: levels.takeProfit,
    stopLoss: levels.stopLoss,
    mu: clamp(toNumber(raw.mu, 0), -5, 5),
    lambda: clamp(toNumber(raw.lambda, 0.5), 0, 2),
    annualVolatility,
    daysForecast,
    simulations,
    spotFlow: clamp(toNumber(raw.spotFlow, 0), -1, 1),
    oiFlow: clamp(toNumber(raw.oiFlow, 0), -1, 1),
    shortLiqAbove: Math.max(0, toNumber(raw.shortLiqAbove, 0)),
    longLiqBelow: Math.max(0, toNumber(raw.longLiqBelow, 0))
  };
}

function validateShortSetup(p) {
  const errors = [];

  if (p.currentPrice <= 0) errors.push('currentPrice must be greater than 0');
  if (p.takeProfit >= p.currentPrice) errors.push('For a short setup, takeProfit should be below currentPrice');
  if (p.stopLoss <= p.currentPrice) errors.push('For a short setup, stopLoss should be above currentPrice');
  if (p.annualVolatility <= 0) errors.push('annualVolatility must be greater than 0');
  if (p.daysForecast <= 0) errors.push('daysForecast must be greater than 0');
  if (p.simulations <= 0) errors.push('simulations must be greater than 0');

  return errors;
}

export function runMonteCarloSimulation(rawParams) {
  const p = normalizeSimulationInput(rawParams);
  const validationErrors = validateShortSetup(p);

  if (validationErrors.length > 0) {
    return {
      ok: false,
      errors: validationErrors,
      repairedInput: p
    };
  }

  const liquidationMagnet = calcLiquidationMagnet(
    p.shortLiqAbove,
    p.longLiqBelow
  );

  const liquidityPressure = calcLiquidityPressure(
    p.spotFlow,
    p.oiFlow,
    liquidationMagnet
  );

  const muAdjusted = p.mu + p.lambda * liquidityPressure;
  const sigma = p.annualVolatility;
  const T = p.daysForecast / 365;
  const n = p.simulations;

  const prices = new Float64Array(n);
  let countDown = 0;
  let countTP = 0;
  let countSL = 0;
  let priceSum = 0;

  const drift = (muAdjusted - 0.5 * sigma * sigma) * T;
  const vol = sigma * Math.sqrt(T);

  for (let i = 0; i < n; i++) {
    const Z = normalRandom();
    const ST = p.currentPrice * Math.exp(drift + vol * Z);

    prices[i] = ST;
    priceSum += ST;

    if (ST < p.currentPrice) countDown++;
    if (ST <= p.takeProfit) countTP++;
    if (ST >= p.stopLoss) countSL++;
  }

  const probDown = countDown / n;
  const probTP = countTP / n;
  const probSL = countSL / n;

  const gain = p.currentPrice - p.takeProfit;
  const loss = p.stopLoss - p.currentPrice;
  const expectedValue = probTP * gain - probSL * loss;
  const expectedValuePct = expectedValue / p.currentPrice;

  const scoreRaw =
    0.4 * probDown +
    0.3 * probTP +
    0.2 * clamp(expectedValuePct, -1, 1) -
    0.1 * probSL;

  const shortEntryScore = clamp(scoreRaw, 0, 1);

  let status = 'NO_SHORT';

  if (probSL > 0.45) {
    status = 'DANGER_STOP_RISK';
  } else if (shortEntryScore >= 0.7 && expectedValue > 0) {
    status = 'SHORT_VALID';
  } else if (shortEntryScore >= 0.55) {
    status = 'SHORT_WATCH';
  }

  const sorted = Array.from(prices).sort((a, b) => a - b);
  const median = percentile(sorted, 0.5);
  const mean = priceSum / n;
  const worst5 = percentile(sorted, 0.05);
  const best5 = percentile(sorted, 0.95);
  const buckets = buildHistogram(sorted, 60);

  return {
    ok: true,
    input: {
      currentPrice: p.currentPrice,
      takeProfit: p.takeProfit,
      stopLoss: p.stopLoss,
      mu: p.mu,
      annualVolatility: p.annualVolatility,
      daysForecast: p.daysForecast,
      simulations: n,
      spotFlow: p.spotFlow,
      oiFlow: p.oiFlow,
      shortLiqAbove: p.shortLiqAbove,
      longLiqBelow: p.longLiqBelow,
      lambda: p.lambda
    },
    liquidity: {
      liquidityPressure,
      liquidationMagnet,
      muAdjusted
    },
    probabilities: {
      probDown,
      probTP,
      probSL
    },
    trade: {
      expectedValue,
      expectedValuePct,
      gain,
      loss,
      riskReward: loss === 0 ? 0 : gain / loss
    },
    score: {
      shortEntryScore,
      scoreRaw,
      scorePercent: Math.round(shortEntryScore * 100),
      status
    },
    stats: {
      median,
      mean,
      worst5,
      best5
    },
    chart: {
      buckets
    }
  };
}

function percentile(sorted, p) {
  if (!sorted.length) return 0;

  const index = clamp(
    Math.floor(sorted.length * p),
    0,
    sorted.length - 1
  );

  return sorted[index];
}

function buildHistogram(sorted, bucketCount) {
  const min = sorted[0];
  const max = sorted[sorted.length - 1];
  const range = max - min;

  if (range === 0) {
    return [
      {
        lower: min,
        upper: max,
        mid: min,
        count: sorted.length,
        density: 1
      }
    ];
  }

  const size = range / bucketCount;

  const buckets = Array.from({ length: bucketCount }, (_, i) => ({
    lower: min + i * size,
    upper: min + (i + 1) * size,
    mid: min + (i + 0.5) * size,
    count: 0,
    density: 0
  }));

  let bucketIndex = 0;

  for (const value of sorted) {
    while (
      bucketIndex < bucketCount - 1 &&
      value >= buckets[bucketIndex].upper
    ) {
      bucketIndex++;
    }

    buckets[bucketIndex].count++;
  }

  for (const bucket of buckets) {
    bucket.density = bucket.count / sorted.length;
  }

  return buckets;
}
