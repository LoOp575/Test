import React from "react";
import { formatCompact, formatCurrency, formatPercent, formatSignedPercent } from "../lib/utils";

export default function MarketSummaryCard({ market }) {
  if (!market) return null;
  const change = market.priceChangePercent || 0;
  const positive = change >= 0;

  const rows = [
    ["24h High", formatCurrency(market.highPrice)],
    ["24h Low", formatCurrency(market.lowPrice)],
    ["24h Range", formatPercent(market.volatility24h || 0, 2)],
    ["Volume (USDT)", formatCompact(market.quoteVolume)],
  ];

  return (
    <div className="panel p-5" data-testid="market-summary-card">
      <div className="data-label mb-4">// Market Summary</div>

      <div className="mb-5">
        <div className="font-display text-2xl text-ink-50 font-medium tracking-tight">
          {market.symbol}
        </div>
        <div className="mt-2 flex items-end gap-3">
          <div className="data-value">{formatCurrency(market.lastPrice)}</div>
          <div
            className={
              "font-mono text-sm tabular-nums pb-1 " +
              (positive ? "text-emerald-400" : "text-rose-400")
            }
            data-testid="market-change-24h"
          >
            {formatSignedPercent(change / 100, 2)}
          </div>
        </div>
      </div>

      <div className="border-t border-ink-600">
        {rows.map(([k, v]) => (
          <div
            key={k}
            className="flex items-center justify-between py-2.5 border-b border-ink-600 last:border-b-0"
          >
            <span className="data-label">{k}</span>
            <span className="font-mono text-sm tabular-nums text-ink-100">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
