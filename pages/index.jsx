/* ============================================================
   Main Dashboard — LQ-Short Hunter
   ============================================================ */

import { useState } from 'react';
import dynamic from 'next/dynamic';
import Head from 'next/head';
import InputPanel from '../components/InputPanel';
import ResultPanel from '../components/ResultPanel';
import ScreenerPanel from '../components/ScreenerPanel';
import { buildSimulationParamsFromMarket } from '../lib/marketLevels';

const DistributionChart = dynamic(
  () => import('../components/DistributionChart'),
  {
    ssr: false,
    loading: () => (
      <div className="chart-card" style={{ height: 400, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>Loading chart...</span>
      </div>
    )
  }
);

export default function Dashboard() {
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedMarket, setSelectedMarket] = useState(null);
  const [autoLevels, setAutoLevels] = useState(null);

  const handleSimulate = async (params, market = selectedMarket, levels = autoLevels) => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/simulate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
      });

      const data = await response.json();

      if (!response.ok || data.ok === false) {
        const message = data.errors?.join(' ') || data.error || 'Simulation failed.';
        throw new Error(message);
      }

      setResults({
        ...data,
        market: market
          ? {
              symbol: market.symbol,
              lastPrice: market.lastPrice,
              priceChangePercent: market.priceChangePercent,
              quoteVolume: market.quoteVolume,
              highPrice: market.highPrice,
              lowPrice: market.lowPrice,
              volatility24h: market.volatility24h
            }
          : null,
        autoLevels: levels || params.autoLevels || null
      });

      if (typeof window !== 'undefined' && window.innerWidth < 1100) {
        setTimeout(() => {
          document.getElementById('results-anchor')?.scrollIntoView({ behavior: 'smooth' });
        }, 120);
      }
    } catch (err) {
      setError(err.message);
      setResults(null);
    } finally {
      setLoading(false);
    }
  };

  const handleSelectMarket = (market) => {
    setSelectedMarket(market);
    const params = buildSimulationParamsFromMarket(market);
    setAutoLevels(params.autoLevels);
  };

  const handleAnalyzeMarket = (market) => {
    setSelectedMarket(market);
    const params = buildSimulationParamsFromMarket(market);
    setAutoLevels(params.autoLevels);
    handleSimulate(params, market, params.autoLevels);
  };

  return (
    <>
      <Head>
        <title>LQ-Short Hunter | Liquidity Reversal Engine</title>
        <meta name="description" content="Probabilistic liquidity short-entry analysis dashboard for BTC and crypto markets" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#9670;</text></svg>" />
      </Head>

      <div className="app-wrapper">
        <header className="header">
          <h1>LQ-SHORT <span>HUNTER</span></h1>
          <p className="subtitle">Pump Exhaustion Auto Analyzer</p>
        </header>

        <main className="dashboard">
          <aside>
            <InputPanel
              onSimulate={(params) => handleSimulate(params)}
              isLoading={loading}
              selectedMarket={selectedMarket}
              autoLevels={autoLevels}
            />
            <div style={{ marginTop: 24 }}>
              <ScreenerPanel
                onSelectSymbol={handleSelectMarket}
                onAnalyzeMarket={handleAnalyzeMarket}
                activeSymbol={selectedMarket?.symbol}
              />
            </div>
          </aside>

          <section>
            {selectedMarket && (
              <div className="selected-market-banner">
                Selected Market: <strong>{selectedMarket.symbol}</strong> @ ${Number(selectedMarket.lastPrice).toLocaleString('en-US')}
                {autoLevels?.exhaustion && (
                  <span className="market-phase">
                    Phase: <strong>{autoLevels.exhaustion.phase}</strong> | Exhaustion: <strong>{Math.round(autoLevels.exhaustion.exhaustionScore * 100)}</strong>
                  </span>
                )}
              </div>
            )}
            {error && <div className="error-banner">{error}</div>}
            <div id="results-anchor" />
            <ResultPanel results={results} />
          </section>
        </main>

        {results?.ok && (
          <section className="chart-section animate-in">
            <DistributionChart results={results} />
          </section>
        )}

        <footer className="disclaimer">
          <div className="disclaimer-box">
            <strong>DISCLAIMER:</strong> This tool is for educational and research purposes only.
            It does <strong>NOT</strong> constitute financial advice, trading signals, or investment recommendations.
            All simulations are based on mathematical models and assumptions that may not reflect real market conditions.
            Monte Carlo simulations use stochastic processes and do not predict future prices. Use at your own risk.
          </div>
        </footer>
      </div>
    </>
  );
}
