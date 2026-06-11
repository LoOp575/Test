import React from "react";
import ScreenerPanel from "../components/ScreenerPanel";
import { TrendingDown, Sigma, Brain, Zap } from "lucide-react";

const FEATURES = [
  { icon: TrendingDown, title: "Pump Exhaustion", desc: "Detects late-stage pump conditions via range, position-in-range and wick rejection." },
  { icon: Sigma, title: "Monte Carlo GBM", desc: "Vectorized 50,000-path Geometric Brownian Motion with liquidity-pressure-adjusted drift." },
  { icon: Zap, title: "Auto TP/SL", desc: "Levels derived from 24h structure + Parkinson volatility — no manual inputs required." },
  { icon: Brain, title: "AI Agent", desc: "Claude Sonnet 4.6 narrative analysis with structured reasoning over the quant payload." },
];

export default function DashboardPage() {
  return (
    <div className="space-y-8 lg:space-y-10">
      {/* Hero */}
      <section className="animate-fade-in" data-testid="hero-section">
        <div className="flex items-end gap-3 mb-3">
          <span className="data-label text-emerald-400">// DASHBOARD</span>
          <span className="h-px flex-1 bg-ink-600 mb-1.5" />
        </div>
        <h1 className="font-display text-4xl sm:text-5xl lg:text-6xl tracking-tight leading-[0.95] text-ink-50">
          Probabilistic <span className="text-emerald-400">short</span> hunting
          <br />
          for crypto markets.
        </h1>
        <p className="mt-5 text-sm sm:text-base text-ink-300 max-w-2xl leading-relaxed">
          Quantitative dashboard combining{" "}
          <span className="text-ink-100">pump exhaustion</span>,{" "}
          <span className="text-ink-100">Monte Carlo simulation</span> and{" "}
          <span className="text-ink-100">AI narrative analysis</span>. Click any token
          to run a full automatic analysis in one shot.
        </p>

        <div className="mt-6 grid grid-cols-2 lg:grid-cols-4 gap-3">
          {FEATURES.map((f) => (
            <div
              key={f.title}
              className="panel p-3.5 flex items-start gap-3 hover:bg-ink-700/60 transition-colors"
              data-testid={`feature-${f.title.toLowerCase().replace(/\s+/g, "-")}`}
            >
              <div className="w-7 h-7 grid place-items-center bg-ink-700 border border-ink-600 rounded-sm flex-shrink-0">
                <f.icon size={14} strokeWidth={1.5} className="text-emerald-400" />
              </div>
              <div className="leading-tight">
                <div className="font-display text-sm text-ink-50 mb-0.5">{f.title}</div>
                <div className="text-[11px] text-ink-300 leading-snug">{f.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Screener */}
      <section data-testid="screener-section">
        <ScreenerPanel />
      </section>
    </div>
  );
}
