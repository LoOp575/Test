import React from "react";
import { formatCurrency, formatNumber, formatPercent, phaseInfo } from "../lib/utils";

export default function PumpExhaustionCard({ auto }) {
  if (!auto || !auto.exhaustion) return null;
  const ex = auto.exhaustion;
  const phase = phaseInfo(ex.phase);
  const exhaustionPct = Math.round((ex.exhaustionScore || 0) * 100);

  const meters = [
    ["Pump Strength", ex.pumpStrength || 0],
    ["High Pressure", ex.highPressure || 0],
    ["Volatility", ex.volatilityStrength || 0],
    ["Volume Score", ex.volumeStrength || 0],
    ["Wick Rejection", ex.wickRatio || 0],
  ];

  return (
    <div className="panel p-5" data-testid="pump-exhaustion-card">
      <div className="data-label mb-4">// Pump Exhaustion</div>

      <div className="flex items-center justify-between mb-2">
        <span className={`badge-base badge-${phase.variant}`} data-testid="phase-badge">
          {phase.label}
        </span>
        <span className="font-mono text-2xl text-ink-50 tabular-nums" data-testid="exhaustion-score">
          {exhaustionPct}
          <span className="text-ink-400 text-sm"> /100</span>
        </span>
      </div>

      <div className="h-1.5 bg-ink-700 rounded-sm overflow-hidden mb-5">
        <div
          className={
            "h-full transition-all duration-700 " +
            (exhaustionPct >= 70
              ? "bg-rose-400"
              : exhaustionPct >= 50
              ? "bg-amber-400"
              : "bg-emerald-400")
          }
          style={{ width: exhaustionPct + "%" }}
        />
      </div>

      <div className="space-y-3 mb-5">
        {meters.map(([label, v]) => (
          <div key={label}>
            <div className="flex items-center justify-between mb-1">
              <span className="font-mono text-[10px] uppercase tracking-wider2 text-ink-300">{label}</span>
              <span className="font-mono text-[11px] tabular-nums text-ink-100">
                {Math.round((v || 0) * 100)}
              </span>
            </div>
            <div className="h-1 bg-ink-700 rounded-sm overflow-hidden">
              <div
                className="h-full bg-ink-300/70"
                style={{ width: Math.round((v || 0) * 100) + "%" }}
              />
            </div>
          </div>
        ))}
      </div>

      <div className="border-t border-ink-600 pt-3">
        <div className="data-label mb-2">Auto Levels</div>
        <Row k="Take Profit" v={formatCurrency(auto.takeProfit)} c="text-emerald-400" />
        <Row k="Stop Loss" v={formatCurrency(auto.stopLoss)} c="text-rose-400" />
        <Row k="Pullback" v={formatPercent(auto.pullbackPct || 0, 2)} />
        <Row k="Buffer" v={formatPercent(auto.stopBufferPct || 0, 2)} />
        <Row k="Risk / Reward" v={formatNumber(auto.riskReward || 0, 2) + "x"} />
      </div>
    </div>
  );
}

function Row({ k, v, c = "" }) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className="font-mono text-[11px] uppercase tracking-wider2 text-ink-300">{k}</span>
      <span className={"font-mono text-sm tabular-nums " + (c || "text-ink-100")}>{v}</span>
    </div>
  );
}
