/* ============================================================
   ScreenerPanel — Binance 24hr USDT market screener
   ============================================================ */

import { useEffect, useMemo, useState } from 'react';
import { formatCurrency, formatNumber } from '../lib/utils';

function normalizeRank(value, max) {
  if (!max || max <= 0) return 0;
  return Math.max(0, Math.min(value / max, 1));
}

function enrichMarketRows(markets) {
  const maxQuoteVolume = Math.max(...markets.map((item) => item.quoteVolume || 0), 1);
  const maxAbsChange = Math.max(...markets.map((item) => Math.abs(item.priceChangePercent || 0)), 1);
  const maxVolatility = Math.max(...markets.map((item) => item.volatility24h || 0), 1);

  return markets.map((item) => {
    const volumeScore = normalizeRank(item.quoteVolume || 0, maxQuoteVolume);
    const changeScore = normalizeRank(Math.abs(item.priceChangePercent || 0), maxAbsChange);
    const volatilityScore = normalizeRank(item.volatility24h || 0, maxVolatility);
    const pumpScore = normalizeRank(Math.max(item.priceChangePercent || 0, 0), maxAbsChange);

    const trendingScore =
      0.4 * volumeScore +
      0.3 * changeScore +
      0.3 * volatilityScore;

    const shortCandidateScore =
      0.35 * pumpScore +
      0.25 * volatilityScore +
      0.25 * volumeScore +
      0.15 * changeScore;

    return {
      ...item,
      volumeScore,
      changeScore,
      volatilityScore,
      pumpScore,
      trendingScore,
      shortCandidateScore
    };
  });
}

export default function ScreenerPanel({ onSelectSymbol, onAnalyzeMarket, activeSymbol }) {
  const [markets, setMarkets] = useState([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [sortBy, setSortBy] = useState('shortCandidates');
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

      setMarkets(enrichMarketRows(json.data || []));
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
      if (sortBy === 'shortCandidates') return b.shortCandidateScore - a.shortCandidateScore;
      if (sortBy === 'trending') return b.trendingScore - a.trendingScore;
      if (sortBy === 'gainers') return b.priceChangePercent - a.priceChangePercent;
      if (sortBy === 'losers') return a.priceChangePercent - b.priceChangePercent;
      if (sortBy === 'volatility') return b.volatility24h - a.volatility24h;
      return b.quoteVolume - a.quoteVolume;
    });

    return rows.slice(0, 80);
  }, [markets, query, sortBy]);

  const quickModes = [
    { key: 'shortCandidates', label: 'Short Candidates' },
    { key: 'trending', label: 'Trending' },
    { key: 'gainers', label: 'Gainers' },
    { key: 'quoteVolume', label: 'Volume' }
  ];

  const openToken = (item) => {
    onAnalyzeMarket?.(item);
    onSelectSymbol?.(item);
  };

  return (
    <div className="card screener-card-wide">
      <div className="card-title">Token Screener</div>

      {error && <div className="error-banner">{error}</div>}

      <div className="screener-tabs">
        {quickModes.map((mode) => (
          <button
            key={mode.key}
            type="button"
            className={`screener-tab ${sortBy === mode.key ? 'active' : ''}`}
            onClick={() => setSortBy(mode.key)}
          >
            {mode.label}
          </button>
        ))}
      </div>

      <div className="form-row screener-controls">
        <div className="form-group">
          <label>Search Symbol</label>
          <input
            type="text"
            placeholder="BTC, ETH, SOL..."
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>

        <div className="form-group">
          <label>Sort By</label>
          <select
            value={sortBy}
            onChange={(event) => setSortBy(event.target.value)}
            className="select-input"
          >
            <option value="shortCandidates">Short Candidates</option>
            <option value="trending">Trending</option>
            <option value="quoteVolume">Top Volume</option>
            <option value="gainers">Top Gainers</option>
            <option value="losers">Top Losers</option>
            <option value="volatility">Top Volatility</option>
          </select>
        </div>
      </div>

      <button type="button" className="btn-secondary" onClick={loadMarkets} disabled={loading}>
        {loading ? 'Loading Market...' : 'Refresh Market Data'}
      </button>

      <div className="screener-table-wrap wide">
        <table className="screener-table">
          <thead>
            <tr>
              <th>Token</th>
              <th>Price</th>
              <th>24h %</th>
              <th>Volume</th>
              <th>Hot</th>
              <th>Open</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((item) => {
              const changeColor = item.priceChangePercent >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
              const isActive = activeSymbol === item.symbol;
              const hotScore = sortBy === 'shortCandidates' ? item.shortCandidateScore : item.trendingScore;

              return (
                <tr
                  key={item.symbol}
                  className={isActive ? 'active-row' : ''}
                  onClick={() => openToken(item)}
                  title="Click token to open automatic analysis"
                >
                  <td>
                    <strong>{item.symbol}</strong>
                    <span className="token-subline">Vol {((item.volatility24h || 0) * 100).toFixed(2)}%</span>
                  </td>
                  <td>{formatCurrency(item.lastPrice)}</td>
                  <td style={{ color: changeColor }}>{item.priceChangePercent.toFixed(2)}%</td>
                  <td>{formatNumber(item.quoteVolume, 0)}</td>
                  <td>
                    <span className="hot-score">{Math.round(hotScore * 100)}</span>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="table-action-btn"
                      onClick={(event) => {
                        event.stopPropagation();
                        openToken(item);
                      }}
                    >
                      Analyze
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="screener-note">
        Klik token untuk membuka halaman analisis otomatis di tab yang sama. Tidak ada parameter manual di halaman utama.
      </p>
    </div>
  );
}
