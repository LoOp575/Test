/* ============================================================
   InputPanel — market parameters + automatic TP/SL indicator
   ============================================================ */

import { useEffect, useState } from 'react';
import { buildSimulationParamsFromMarket } from '../lib/marketLevels';
import { formatCurrency, formatNumber, formatPercent } from '../lib/utils';

export const DEFAULT_INPUTS = {
  currentPrice: 105000,
  mu: -0.05,
  annualVolatility: 0.6,
  daysForecast: 7,
  simulations: 50000,
  spotFlow: 0.3,
  oiFlow: 0.2,
  shortLiqAbove: 500000000,
  longLiqBelow: 300000000,
  takeProfit: 100000,
  stopLoss: 110000,
  lambda: 0.5
};

function buildManualFallback(values) {
  const price = Number(values.currentPrice);

  if (!Number.isFinite(price) || price <= 0) {
    return values;
  }

  return {
    ...values,
    takeProfit: price * 0.96,
    stopLoss: price * 1.03
  };
}

export default function InputPanel({ onSimulate, isLoading, selectedMarket, autoLevels }) {
  const [values, setValues] = useState(DEFAULT_INPUTS);

  useEffect(() => {
    if (!selectedMarket?.lastPrice) return;

    const params = buildSimulationParamsFromMarket(selectedMarket);

    setValues((prev) => ({
      ...prev,
      currentPrice: params.currentPrice,
      mu: params.mu,
      annualVolatility: params.annualVolatility,
      spotFlow: params.spotFlow,
      oiFlow: params.oiFlow,
      takeProfit: params.takeProfit,
      stopLoss: params.stopLoss,
      lambda: params.lambda
    }));
  }, [selectedMarket]);

  const setValue = (key) => (event) => {
    const nextValue = Number(event.target.value);
    setValues((prev) => ({
      ...prev,
      [key]: Number.isFinite(nextValue) ? nextValue : 0
    }));
  };

  const handleSubmit = (event) => {
    event.preventDefault();

    const params = selectedMarket
      ? buildSimulationParamsFromMarket(selectedMarket)
      : buildManualFallback(values);

    const finalValues = {
      ...values,
      ...params,
      simulations: values.simulations,
      daysForecast: values.daysForecast
    };

    setValues(finalValues);
    onSimulate(finalValues);
  };

  const resetDefaults = () => setValues({ ...DEFAULT_INPUTS });

  const displayedAutoLevels = autoLevels || (selectedMarket ? buildSimulationParamsFromMarket(selectedMarket).autoLevels : null);

  return (
    <div className="card">
      <div className="card-title">Auto Analyzer Parameters</div>

      <form onSubmit={handleSubmit}>
        <div className="form-section">
          <div className="form-section-title">Market Parameters</div>
          <div className="form-row">
            <div className="form-group">
              <label>Current Market Price ($)</label>
              <input type="number" step="any" value={values.currentPrice} onChange={setValue('currentPrice')} />
            </div>
            <div className="form-group">
              <label>Days Forecast</label>
              <input type="number" min="1" max="365" value={values.daysForecast} onChange={setValue('daysForecast')} />
            </div>
          </div>
          <div className="form-group">
            <label>Simulations (1,000 – 100,000)</label>
            <input type="number" step="1000" min="1000" max="100000" value={values.simulations} onChange={setValue('simulations')} />
          </div>
        </div>

        <div className="form-section">
          <div className="form-section-title">Pump Exhaustion Indicator</div>

          {displayedAutoLevels?.exhaustion ? (
            <div className="auto-level-box">
              <div className="auto-level-row">
                <span>Phase</span>
                <strong>{displayedAutoLevels.exhaustion.phase}</strong>
              </div>
              <div className="auto-level-row">
                <span>Exhaustion Score</span>
                <strong>{Math.round(displayedAutoLevels.exhaustion.exhaustionScore * 100)} / 100</strong>
              </div>
              <div className="auto-level-row">
                <span>Pump Strength</span>
                <strong>{Math.round(displayedAutoLevels.exhaustion.pumpStrength * 100)} / 100</strong>
              </div>
              <div className="auto-level-row">
                <span>Position in 24h Range</span>
                <strong>{Math.round(displayedAutoLevels.exhaustion.positionInRange * 100)}%</strong>
              </div>
              <div className="auto-level-row">
                <span>Auto Take Profit</span>
                <strong className="green">{formatCurrency(displayedAutoLevels.takeProfit)}</strong>
              </div>
              <div className="auto-level-row">
                <span>Auto Stop Loss</span>
                <strong className="red">{formatCurrency(displayedAutoLevels.stopLoss)}</strong>
              </div>
              <div className="auto-level-row">
                <span>Expected Pullback</span>
                <strong>{formatPercent(displayedAutoLevels.pullbackPct)}</strong>
              </div>
              <div className="auto-level-row">
                <span>Auto Risk/Reward</span>
                <strong>{formatNumber(displayedAutoLevels.riskReward, 2)}</strong>
              </div>
            </div>
          ) : (
            <p className="screener-note">
              Pilih token dari screener. Sistem akan menghitung TP/SL otomatis dari rumus pump exhaustion.
            </p>
          )}
        </div>

        <button type="submit" className="btn-primary" disabled={isLoading}>
          {isLoading ? (
            <>
              <span className="spinner" /> Analyzing...
            </>
          ) : selectedMarket ? (
            `Analyze ${selectedMarket.symbol}`
          ) : (
            'Run Auto Simulation'
          )}
        </button>

        <button type="button" onClick={resetDefaults} className="btn-secondary" style={{ marginTop: 10 }}>
          Reset Defaults
        </button>
      </form>
    </div>
  );
}
