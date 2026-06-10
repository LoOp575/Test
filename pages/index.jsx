/* ============================================================
   Main Screener — LQ-Short Hunter
   ============================================================ */

import Head from 'next/head';
import ScreenerPanel from '../components/ScreenerPanel';

export default function Dashboard() {
  const openAnalysisTab = (market) => {
    if (!market?.symbol || typeof window === 'undefined') return;
    window.open(`/analyze/${encodeURIComponent(market.symbol)}`, '_blank', 'noopener,noreferrer');
  };

  return (
    <>
      <Head>
        <title>LQ-Short Hunter | Token Screener</title>
        <meta name="description" content="Pump exhaustion token screener and automatic Monte Carlo analyzer" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#9670;</text></svg>" />
      </Head>

      <div className="app-wrapper">
        <header className="header">
          <h1>LQ-SHORT <span>HUNTER</span></h1>
          <p className="subtitle">Token Screener — Click Token To Analyze</p>
        </header>

        <main style={{ padding: '24px 32px', maxWidth: 1320, margin: '0 auto' }}>
          <ScreenerPanel
            onSelectSymbol={openAnalysisTab}
            onAnalyzeMarket={openAnalysisTab}
          />
        </main>

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
