/* ============================================================
   Market Auto Levels

   Builds automatic short TP/SL levels from 24h market structure.
   Goal: detect when a pump is getting tired and estimate a
   probabilistic pullback zone without manual TP/SL input.
   ============================================================ */

import { clamp, toNumber } from './utils';

export function calcPumpExhaustion(market = {}) {
  const price = Math.max(0, toNumber(market.lastPrice, market.currentPrice || 0));
  const high = Math.max(price, toNumber(market.highPrice, price));
  const low = Math.max(0, Math.min(price, toNumber(market.lowPrice, price)));
  const changePct = toNumber(market.priceChangePercent, 0) / 100;
  const quoteVolume = Math.max(0, toNumber(market.quoteVolume, 0));

  const range = Math.max(high - low, 0);
  const rangePct = price > 0 ? range / price : 0;
  const positionInRange = range > 0 ? clamp((price - low) / range, 0, 1) : 0.5;

  const pumpStrength = clamp(Math.max(changePct, 0) / 0.25, 0, 1); // 25%+ pump = extreme
  const volatilityStrength = clamp(rangePct / 0.18, 0, 1); // 18%+ 24h range = hot
  const highPressure = clamp(positionInRange, 0, 1); // close to 24h high = late pump
  const volumeStrength = clamp(Math.log10(quoteVolume + 1) / 10, 0, 1);

  const exhaustionScore = clamp(
    0.35 * pumpStrength +
    0.25 * highPressure +
    0.25 * volatilityStrength +
    0.15 * volumeStrength,
    0,
    1
  );

  let phase = 'NORMAL';
  if (exhaustionScore >= 0.75) phase = 'PUMP_EXHAUSTED';
  else if (exhaustionScore >= 0.58) phase = 'PUMP_TIRED';
  else if (exhaustionScore >= 0.42) phase = 'PUMP_WATCH';

  return {
    price,
    high,
    low,
    range,
    rangePct,
    changePct,
    positionInRange,
    pumpStrength,
    volatilityStrength,
    highPressure,
    volumeStrength,
    exhaustionScore,
    phase
  };
}

export function buildAutoShortLevels(market = {}) {
  const x = calcPumpExhaustion(market);
  const price = x.price;

  if (!price || price <= 0) {
    return {
      ok: false,
      reason: 'Invalid market price',
      takeProfit: 0,
      stopLoss: 0,
      exhaustion: x
    };
  }

  // Pullback target: the more exhausted the pump, the deeper the expected pullback.
  // Minimum 1.5%, maximum 18% so meme coins still get usable levels.
  const pullbackPct = clamp(
    0.015 +
    0.42 * x.rangePct +
    0.055 * x.exhaustionScore,
    0.015,
    0.18
  );

  // Stop buffer: above current price / above the recent high when price is near high.
  // This avoids placing SL too tight during squeeze conditions.
  const stopBufferPct = clamp(
    0.01 +
    0.22 * x.rangePct +
    0.035 * (1 - x.exhaustionScore),
    0.01,
    0.12
  );

  const rangePullbackTarget = x.range > 0 ? price - x.range * (0.28 + 0.22 * x.exhaustionScore) : price * (1 - pullbackPct);
  const percentPullbackTarget = price * (1 - pullbackPct);

  let takeProfit = Math.min(rangePullbackTarget, percentPullbackTarget);
  takeProfit = Math.max(takeProfit, price * 0.55); // safety floor
  takeProfit = Math.min(takeProfit, price * 0.985); // always below entry

  const highBasedStop = x.high > price ? x.high * 1.003 : price * (1 + stopBufferPct);
  const percentStop = price * (1 + stopBufferPct);

  let stopLoss = Math.max(highBasedStop, percentStop);
  stopLoss = Math.min(stopLoss, price * 1.25); // avoid absurd stop on wild seed data
  stopLoss = Math.max(stopLoss, price * 1.01); // always above entry

  const gain = price - takeProfit;
  const loss = stopLoss - price;
  const riskReward = loss > 0 ? gain / loss : 0;

  return {
    ok: true,
    takeProfit,
    stopLoss,
    pullbackPct,
    stopBufferPct,
    gain,
    loss,
    riskReward,
    exhaustion: x
  };
}

export function buildSimulationParamsFromMarket(market = {}) {
  const levels = buildAutoShortLevels(market);
  const x = levels.exhaustion;
  const price = x.price;

  return {
    currentPrice: price,
    mu: x.changePct > 0 ? -0.02 - 0.04 * x.exhaustionScore : 0,
    annualVolatility: clamp(Math.max(x.rangePct, 0.01) * Math.sqrt(365), 0.1, 5),
    daysForecast: 7,
    simulations: 50000,

    // Spot flow is treated as overheated when pump exhaustion is high.
    // Positive means recent buying pressure; the short engine will still judge TP/SL probability.
    spotFlow: clamp(0.1 + 0.55 * x.pumpStrength - 0.35 * x.exhaustionScore, -1, 1),
    oiFlow: 0,

    // Binance 24h endpoint has no liquidation data, so keep neutral until futures/liquidation APIs are added.
    shortLiqAbove: 0,
    longLiqBelow: 0,

    takeProfit: levels.takeProfit,
    stopLoss: levels.stopLoss,
    lambda: 0.5,

    autoLevels: levels
  };
}
