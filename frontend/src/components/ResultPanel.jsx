import React from "react";
import ScoreGauge from "./ScoreGauge";
import StatusBadge from "./StatusBadge";
import MetricCard from "./MetricCard";
import {
  formatCurrency,
  formatNumber,
  formatPercent,
  scoreColor,
  statusInfo,
} from "../lib/utils";

export default function ResultPanel({ results }) {
  if (!results) {
    return (
      <div className="panel p-12 text-center" data-testid="result-empty">
        <p className="text-ink-300 text-sm">No simulation data yet.</p>
      </div>
    );
  }
  if (results.ok === false) {
    return (
      <div className="panel p-5" data-testid="result-error">
        <div className="data-label mb-2">// Analysis Results</div>
        <div className="text-sm text-rose-400">
          {(results.errors || [results.error || "Simulation failed."]).join(" ")}
        </div>
      </div>
    );
  }

  const status = results.score?.status || "NO_SHORT";
  const info = statusInfo(status);
  const score01 = results.score?.shortEntryScore || 0;
  const color = scoreColor(score01);

  const probDown = results.probabilities?.probDown || 0;
  const probTP = results.probabilities?.probTP || 0;
  const probSL = results.probabilities?.probSL || 0;
  const ev = results.trade?.expectedValue || 0;
  const evPct = results.trade?.expectedValuePct || 0;
  const rr = results.trade?.riskReward || 0;

  const mu = results.liquidity?.muAdjusted || 0;
  const lp = results.liquidity?.liquidityPressure || 0;
  const lm = results.liquidity?.liquidationMagnet || 0;

  const mean = results.stats?.mean || 0;
  const median = results.stats?.median || 0;
  const worst5 = results.stats?.worst5 || 0;
  const best5 = results.stats?.best5 || 0;

  return (
    <div className="panel p-5 sm:p-6" data-testid="result-panel">
      <div className="data-label mb-4">// Analysis Result</div>

      <div className="grid grid-cols-1 md:grid-cols-[auto_1fr] gap-6 items-center">
        <div className="grid place-items-center" data-testid="score-gauge-wrap">
          <ScoreGauge score={score01} color={color} />
        </div>

        <div className="space-y-3">
          <StatusBadge status={status} color={info.color} />
          <p className="text-sm text-ink-300 leading-relaxed">
            Probability-weighted short setup for the current pump structure.
            Higher score = stronger directional + structural edge.
          </p>
          <div className="grid grid-cols-3 gap-3 text-center">
            <Pill label="Expected Value" value={formatPercent(evPct, 2)} tone={ev >= 0 ? "up" : "down"} />
            <Pill label="Risk / Reward" value={formatNumber(rr, 2) + "x"} tone={rr >= 1 ? "up" : "warn"} />
            <Pill label="Down Bias" value={formatPercent(probDown, 1)} tone={probDown > 0.5 ? "up" : "warn"} />
          </div>
        </div>
      </div>

      {/* Metrics grid */}
      <div className="mt-7">
        <div className="data-label mb-3">// Metrics</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <MetricCard
            label="Probability TP"
            value={formatPercent(probTP, 2)}
            progress={probTP}
            tone="up"
            testid="metric-prob-tp"
          />
          <MetricCard
            label="Probability SL"
            value={formatPercent(probSL, 2)}
            progress={probSL}
            tone="down"
            testid="metric-prob-sl"
          />
          <MetricCard
            label="Expected Value"
            value={formatCurrency(ev)}
            tone={ev >= 0 ? "up" : "down"}
            testid="metric-ev"
          />
          <MetricCard
            label="Mean Price"
            value={formatCurrency(mean)}
            testid="metric-mean"
          />
          <MetricCard
            label="Median Price"
            value={formatCurrency(median)}
            testid="metric-median"
          />
          <MetricCard
            label="Worst 5%"
            value={formatCurrency(worst5)}
            tone="down"
            testid="metric-worst5"
          />
          <MetricCard
            label="Best 5%"
            value={formatCurrency(best5)}
            tone="up"
            testid="metric-best5"
          />
          <MetricCard
            label="μ Adjusted"
            value={formatNumber(mu, 4)}
            tone={mu < 0 ? "up" : "warn"}
            testid="metric-mu"
          />
          <MetricCard
            label="Liquidity Pressure"
            value={formatNumber(lp, 4)}
            progress={(lp + 1) / 2}
            testid="metric-liquidity-pressure"
          />
          <MetricCard
            label="Liquidation Magnet"
            value={formatNumber(lm, 4)}
            progress={(lm + 1) / 2}
            testid="metric-liquidation-magnet"
          />
        </div>
      </div>
    </div>
  );
}

function Pill({ label, value, tone }) {
  const tones = {
    up:   "text-emerald-400 border-emerald-400/20 bg-emerald-400/5",
    down: "text-rose-400 border-rose-400/20 bg-rose-400/5",
    warn: "text-amber-400 border-amber-400/20 bg-amber-400/5",
  };
  return (
    <div className={"border rounded-sm py-2 " + (tones[tone] || tones.warn)}>
      <div className="font-mono text-[9px] uppercase tracking-wider2 opacity-80">{label}</div>
      <div className="font-mono text-sm font-semibold tabular-nums mt-0.5">{value}</div>
    </div>
  );
}
