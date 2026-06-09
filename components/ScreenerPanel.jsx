/* ============================================================
   ScreenerPanel — Binance 24hr USDT market screener
   ============================================================ */

import { useEffect, useMemo, useState } from 'react';
import { formatCurrency, formatNumber } from '../lib/utils';

export default function ScreenerPanel({ onSelectSymbol }) {
  const [markets, setMarkets] = useState([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [sortBy, setSortBy] = useState('quoteVolume');
  const [error, setError] = useState(null);

  async function loadMarkets() {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/binance24hr');
      const json = await response.json();

      if (!response.ok || !json.ok) {
        throw new Error(json.error || 'Failed to load Binance data.');
      }

      setMarkets(json.data || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadMarkets();
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toUpperCase();

    let rows = markets.filter((item) => {
      if (!q) return true;
      return item.symbol.includes(q);
    });

    rows = [...rows].sort((a, b) => {
      if (sortBy === 'gainers') return b.priceChangePercent - a.priceChangePercent;
      if (sortBy === 'losers') return a.priceChangePercent - b.priceChangePercent;
      if (sortBy === 'volatility') return b.volatility24h - a.volatility24h;
      return b.quoteVolume - a.quoteVolume;
    });

    return rows.slice(0, 30);
  }, [markets, query, sortBy]);

  return (
    <div className="card">
      <div className="card-title">Binance USDT Screener</div>

      {error && <div className="error-banner">{error}</div>}

      <div className="form-row">
        <div className="form-group">
          <label>Search Symbol</label>
          <input type="text" placeholder="BTC, ETH, SOL..." value={query} onChange={(event) => setQuery(event.target.value)} />
        </div>

        <div className="form-group">
          <label>Sort By</label>
          <select value={sortBy} onChange={(event) => setSortBy(event.target.value)} className="select-input">
            <option value="quoteVolume">Top Volume</option>
            <option value="gainers">Top Gainers</option>
            <option value="losers">Top Losers</option>
            <option value="volatility">Top Volatility</option>
          </select>
        </div>
      </div>

      <button type="button" className="btn-secondary" onClick={loadMarkets} disabled={loading}>
        {loading ? 'Loading Market...' : 'Refresh Binance Data'}
      </button>

      <div className="screener-table-wrap">
        <table className="screener-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Price</th>
              <th>24h %</th>
              <th>Volume USDT</th>
              <th>Volatility</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((item) => {
              const changeColor = item.priceChangePercent >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

              return (
                <tr key={item.symbol} onClick={() => onSelectSymbol?.(item)} title="Click to use this market price">
                  <td>{item.symbol}</td>
                  <td>{formatCurrency(item.lastPrice)}</td>
                  <td style={{ color: changeColor }}>{item.priceChangePercent.toFixed(2)}%</td>
                  <td>{formatNumber(item.quoteVolume, 0)}</td>
                  <td>{(item.volatility24h * 100).toFixed(2)}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="screener-note">Click a symbol to send its latest price into the short engine.</p>
    </div>
  );
}
