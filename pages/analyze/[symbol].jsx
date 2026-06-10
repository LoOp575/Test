/* ============================================================
   Token Analysis Page — opens from the screener in a new tab
   ============================================================ */

import { useEffect, useMemo, useState } from 'react';
import dynamic from 'next/dynamic';
import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import ResultPanel from '../../components/ResultPanel';
import { buildSimulationParamsFromMarket } from '../../lib/marketLevels';
import { formatCurrency, formatNumber, formatPercent } from '../../lib/utils';

const DistributionChart = dynamic(
  () => import('../../components/DistributionChart'),
  {
    ssr: false,
    loading: () => (
      <div className="chart-card" style={{ height: 400, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>Loading chart...</span>
      </div>
    )
  }
);

function normalizeSymbol(value) {
  return String(value || '').trim().toUpperCase();
}

export default function TokenAnalysisPage() {
  const router = useRouter();
  const symbol = normalizeSymbol(router.query.symbol);

  const [market, setMarket] = useState(null);
  const [autoLevels, setAutoLevels] = useState(null);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const marketSummary = useMemo(() => {
    if (!market) return [];

    return [
      ['Price', formatCurrency(market.lastPrice)],
      ['24h Change', `${formatNumber(market.priceChangePercent, 2)}%`],
      ['24h High', formatCurrency(market.highPrice)],
      ['24h Low', formatCurrency(market.lowPrice)],
      ['24h Volatility', formatPercent(market.volatility24h || 0)],
      ['Volume', formatNumber(market.quoteVolume, 0)]
    ];
  }, [market]);

  useEffect(() => {
    if (!symbol) return;

    async function runAnalysis() {
      setLoading(true);
      setError(null);
      setResults(null);
      setMarket(null);
      setAutoLevels(null);

      try {
        const marketResponse = await fetch('/api/binance24hr');
        const marketJson = await marketResponse.json();

        if (!marketResponse.ok || !marketJson.ok) {
          throw new Error(marketJson.error || 'Failed to load market data.');
        }

        const found = (marketJson.data || []).find((item) => item.symbol === symbol);

        if (!found) {
          throw new Error(`${symbol} tidak ditemukan di market list.`);
        }

        const params = buildSimulationParamsFromMarket(found);
        setMarket(found);
        setAutoLevels(params.autoLevels);

        const simResponse = await fetch('/api/simulate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params)
        });

        const simJson = await simResponse.json();

        if (!simResponse.ok || simJson.ok === false) {
          const message = simJson.errors?.join(' ') || simJson.error || 'Simulation failed.';
          throw new Error(message);
        }

        setResults({
          ...simJson,
          market: found,
          autoLevels: params.autoLevels
        });
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }

    runAnalysis();
  }, [symbol]);

  return (
    <>
      <Head>
        <title>{symbol ? `${symbol} Analysis` : 'Token Analysis'} | LQ-Short Hunter</title>
        <meta name="description" content="Automatic pump exhaustion and Monte Carlo analysis page" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>

      <div className="app-wrapper">
        <header className="header">
          <h1>LQ-SHORT <span>ANALYZE</span></h1>
          <p className="subtitle">Automatic Token Analysis</p>
        </header>

        <main className="analysis-page">
          <div className="analysis-topbar">
            <Link href="/" className="back-link">← Back to Screener</Link>
            {symbol && <span className="analysis-symbol">{symbol}</span>}
          </div>

          {loading && (
            <div className="card">
              <div className="placeholder">
                <div>
                  <div className="placeholder-icon">◆</div>
                  <p className="placeholder-text">Loading market data and running automatic analysis...</p>
                </div>
              </div>
            </div>
          )}

          {error && <div className="error-banner">{error}</div>}

          {!loading && market && (
            <section className="analysis-layout">
              <aside className="card analysis-info-card">
                <div className="card-title">Market Summary</div>

                <div className="analysis-market-title">
                  <strong>{market.symbol}</strong>
                  <span>{formatCurrency(market.lastPrice)}</span>
                </div>

                <div className="auto-level-box">
                  {marketSummary.map(([label, value]) => (
                    <div className="auto-level-row" key={label}>
                      <span>{label}</span>
                      <strong>{value}</strong>
                    </div>
                  ))}
                </div>

                {autoLevels?.exhaustion && (
                  <div style={{ marginTop: 18 }}>
                    <div className="card-title">Pump Exhaustion</div>
                    <div className="auto-level-box">
                      <div className="auto-level-row">
                        <span>Phase</span>
                        <strong>{autoLevels.exhaustion.phase}</strong>
                      </div>
                      <div className="auto-level-row">
                        <span>Exhaustion</span>
                        <strong>{Math.round(autoLevels.exhaustion.exhaustionScore * 100)}</strong>
                      </div>
                      <div className="auto-level-row">
                        <span>Auto TP</span>
                        <strong className="green">{formatCurrency(autoLevels.takeProfit)}</strong>
                      </div>
                      <div className="auto-level-row">
                        <span>Auto SL</span>
                        <strong className="red">{formatCurrency(autoLevels.stopLoss)}</strong>
                      </div>
                      <div className="auto-level-row">
                        <span>Risk / Reward</span>
                        <strong>{formatNumber(autoLevels.riskReward, 2)}</strong>
                      </div>
                    </div>
                  </div>
                )}
              </aside>

              <section>
                <ResultPanel results={results} />
              </section>
            </section>
          )}

          {results?.ok && (
            <section className="chart-section analysis-chart-section animate-in">
              <DistributionChart results={results} />
            </section>
          )}
        </main>

        <footer className="disclaimer">
          <div className="disclaimer-box">
            <strong>DISCLAIMER:</strong> Educational/research only. Not financial advice, trading signal, or investment recommendation.
          </div>
        </footer>
      </div>
    </>
  );
}
