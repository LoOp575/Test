/* ============================================================
   MetricCard — single metric display with optional progress bar
   ============================================================ */

import { clamp } from '../lib/utils';

export default function MetricCard({
  label,
  value,
  color,
  progress,
  progressColor
}) {
  const hasProgress = progress !== undefined && progress !== null;
  const safeProgress = hasProgress ? clamp(Number(progress), 0, 1) : 0;

  return (
    <div className="metric-card">
      <div className="m-label">{label}</div>
      <div className={`m-value ${color || ''}`}>{value}</div>
      {hasProgress && (
        <div className="progress-bar">
          <div
            className="fill"
            style={{
              width: `${safeProgress * 100}%`,
              background: progressColor || 'var(--accent-cyan)'
            }}
          />
        </div>
      )}
    </div>
  );
}
