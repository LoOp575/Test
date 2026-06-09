/* ============================================================
   ResultPanel — displays all calculated analysis results
   ============================================================ */

import ScoreGauge from './ScoreGauge';
import StatusBadge from './StatusBadge';
import MetricCard from './MetricCard';
import {
  clamp,
  formatCurrency,
  formatNumber,
  formatPercent,
  getStatusColor
} from '../lib/utils';

export default function ResultPanel({ results }) {
  if (!results) {
    return (
      <div className="card">
        <div className="placeholder">
          <div>
            <div className="placeholder-icon">&#9670;</div>
            <p className="placeholder-text">
              Configure parameters and run<br />the simulation to see results.
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (results.ok === false) {
    return (
      <div className="card">
        <div className="card-title">Analysis Results</div>
        <div className="error-banner">
          {(results.errors || [results.error || 'Simulation failed.']).join(' ')}
        </div>
      </div>
    );
  }

  const status = results.score?.status || 'NO_SHORT';
  const shortEntryScore = results.score?.shortEntryScore || 0;
  const statusColor = getStatusColor(status);

  const probDown = results.probabilities?.probDown || 0;
  const probTP = results.probabilities?.probTP || 0;
  const probSL = results.probabilities?.probSL || 0;

  const expectedValue = results.trade?.expectedValue || 0;
  const riskReward = results.trade?.riskReward || 0;

  const liquidityPressure = results.liquidity?.liquidityPressure || 0;
  const liquidationMagnet = results.liquidity?.liquidationMagnet || 0;
  const muAdjusted = results.liquidity?.muAdjusted || 0;

  const median = results.stats?.median || 0;
  const mean = results.stats?.mean || 0;
  const worst5 = results.stats?.worst5 || 0;
  const best5 = results.stats?.best5 || 0;

  const expectedValueColor = expectedValue >= 0 ? 'green' : 'red';
  const liquidityPressureProgress = clamp((liquidityPressure + 1) / 2, 0, 1);
  const liquidationMagnetProgress = clamp((liquidationMagnet + 1) / 2, 0, 1);

  return (
    <div className="card animate-in">
      <div className="card-title">Analysis Results</div>

      <ScoreGauge score={shortEntryScore} color={statusColor} />
      <StatusBadge status={status} />

      <div className="results-grid">
        <MetricCard label="Probability Down" value={formatPercent(probDown)} color="cyan" progress={probDown} progressColor="var(--accent-cyan)" />
        <MetricCard label="Probability TP" value={formatPercent(probTP)} color="green" progress={probTP} progressColor="var(--accent-green)" />
        <MetricCard label="Probability SL" value={formatPercent(probSL)} color="red" progress={probSL} progressColor="var(--accent-red)" />
        <MetricCard label="Expected Value" value={formatCurrency(expectedValue)} color={expectedValueColor} />
        <MetricCard label="Risk / Reward" value={formatNumber(riskReward, 2)} color={riskReward >= 1 ? 'green' : 'amber'} />
        <MetricCard label="Mu Adjusted" value={formatNumber(muAdjusted, 4)} color={muAdjusted >= 0 ? 'cyan' : 'amber'} />
        <MetricCard
          label="Liquidity Pressure"
          value={formatNumber(liquidityPressure, 4)}
          color={liquidityPressure >= 0 ? 'cyan' : 'amber'}
          progress={liquidityPressureProgress}
          progressColor={liquidityPressure >= 0 ? 'var(--accent-cyan)' : 'var(--accent-amber)'}
        />
        <MetricCard
          label="Liquidation Magnet"
          value={formatNumber(liquidationMagnet, 4)}
          color={liquidationMagnet >= 0 ? 'cyan' : 'amber'}
          progress={liquidationMagnetProgress}
          progressColor={liquidationMagnet >= 0 ? 'var(--accent-cyan)' : 'var(--accent-amber)'}
        />
        <MetricCard label="Mean Price" value={formatCurrency(mean)} />
        <MetricCard label="Median Price" value={formatCurrency(median)} />
        <MetricCard label="Worst 5%" value={formatCurrency(worst5)} color="red" />
        <MetricCard label="Best 5%" value={formatCurrency(best5)} color="green" />
      </div>
    </div>
  );
}
