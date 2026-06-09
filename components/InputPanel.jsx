/* ============================================================
   InputPanel — all user-editable simulation parameters
   ============================================================ */

import { useEffect, useState } from 'react';

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

export default function InputPanel({ onSimulate, isLoading, selectedMarket }) {
  const [values, setValues] = useState(DEFAULT_INPUTS);

  useEffect(() => {
    if (!selectedMarket?.lastPrice) return;

    const price = Number(selectedMarket.lastPrice);
    const annualizedVolatility = Math.max(
      0.1,
      Math.min((selectedMarket.volatility24h || 0.05) * Math.sqrt(365), 5)
    );

    setValues((prev) => ({
      ...prev,
      currentPrice: price,
      annualVolatility: Number(annualizedVolatility.toFixed(4)),
      takeProfit: Number((price * 0.96).toFixed(2)),
      stopLoss: Number((price * 1.03).toFixed(2))
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
    onSimulate(values);
  };

  const resetDefaults = () => setValues({ ...DEFAULT_INPUTS });

  return (
    <div className="card">
      <div className="card-title">Input Parameters</div>

      <form onSubmit={handleSubmit}>
        <div className="form-section">
          <div className="form-section-title">Market Parameters</div>
          <div className="form-row">
            <div className="form-group">
              <label>Current BTC / Market Price ($)</label>
              <input type="number" value={values.currentPrice} onChange={setValue('currentPrice')} />
            </div>
            <div className="form-group">
              <label>Mu / Base Return</label>
              <input type="number" step="0.01" value={values.mu} onChange={setValue('mu')} />
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>Annual Volatility</label>
              <input type="number" step="0.01" min="0.01" value={values.annualVolatility} onChange={setValue('annualVolatility')} />
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
          <div className="form-section-title">Liquidity Data</div>
          <div className="form-row">
            <div className="form-group">
              <label>Spot Flow Score (-1 to 1)</label>
              <input type="number" step="0.1" min="-1" max="1" value={values.spotFlow} onChange={setValue('spotFlow')} />
            </div>
            <div className="form-group">
              <label>OI Flow Score (-1 to 1)</label>
              <input type="number" step="0.1" min="-1" max="1" value={values.oiFlow} onChange={setValue('oiFlow')} />
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>Short Liquidation Above ($)</label>
              <input type="number" min="0" value={values.shortLiqAbove} onChange={setValue('shortLiqAbove')} />
            </div>
            <div className="form-group">
              <label>Long Liquidation Below ($)</label>
              <input type="number" min="0" value={values.longLiqBelow} onChange={setValue('longLiqBelow')} />
            </div>
          </div>
        </div>

        <div className="form-section">
          <div className="form-section-title">Trade Setup</div>
          <div className="form-row">
            <div className="form-group">
              <label>Take Profit Level ($)</label>
              <input type="number" min="0" value={values.takeProfit} onChange={setValue('takeProfit')} />
            </div>
            <div className="form-group">
              <label>Stop Loss Level ($)</label>
              <input type="number" min="0" value={values.stopLoss} onChange={setValue('stopLoss')} />
            </div>
          </div>
          <div className="form-group">
            <label>Lambda / Liquidity Influence</label>
            <input type="number" step="0.1" min="0" max="2" value={values.lambda} onChange={setValue('lambda')} />
          </div>
        </div>

        <button type="submit" className="btn-primary" disabled={isLoading}>
          {isLoading ? (
            <>
              <span className="spinner" /> Simulating...
            </>
          ) : (
            'Run Simulation'
          )}
        </button>

        <button type="button" onClick={resetDefaults} className="btn-secondary" style={{ marginTop: 10 }}>
          Reset Defaults
        </button>
      </form>
    </div>
  );
}
