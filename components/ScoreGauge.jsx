/* ============================================================
   ScoreGauge — SVG circular gauge (0–100)
   ============================================================ */

import { useEffect, useId, useState } from 'react';
import { clamp } from '../lib/utils';

export default function ScoreGauge({ score = 0, color = '#00e5ff' }) {
  const safeScore = clamp(score, 0, 1);
  const [anim, setAnim] = useState(0);

  const rawId = useId();
  const safeId = rawId.replace(/[^a-zA-Z0-9_-]/g, '');
  const gradientId = `gGrad-${safeId}`;
  const glowId = `gGlow-${safeId}`;

  useEffect(() => {
    setAnim(0);
    const timer = setTimeout(() => setAnim(safeScore), 80);
    return () => clearTimeout(timer);
  }, [safeScore]);

  const r = 82;
  const sw = 10;
  const circ = 2 * Math.PI * r;
  const offset = circ - anim * circ;
  const display = Math.round(anim * 100);

  return (
    <div className="gauge-container">
      <svg width="200" height="200" viewBox="0 0 200 200">
        <defs>
          <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor={color} stopOpacity="0.7" />
            <stop offset="100%" stopColor={color} />
          </linearGradient>
          <filter id={glowId}>
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <circle cx="100" cy="100" r={r} fill="none" stroke="rgba(255,255,255,0.035)" strokeWidth={sw} />

        {Array.from({ length: 24 }, (_, i) => {
          const angle = ((i / 24) * 360 - 90) * (Math.PI / 180);
          const r1 = r - 16;
          const r2 = r - 11;
          return (
            <line
              key={i}
              x1={100 + r1 * Math.cos(angle)}
              y1={100 + r1 * Math.sin(angle)}
              x2={100 + r2 * Math.cos(angle)}
              y2={100 + r2 * Math.sin(angle)}
              stroke="rgba(255,255,255,0.06)"
              strokeWidth="1"
            />
          );
        })}

        <circle
          cx="100"
          cy="100"
          r={r}
          fill="none"
          stroke={`url(#${gradientId})`}
          strokeWidth={sw}
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={offset}
          transform="rotate(-90 100 100)"
          filter={`url(#${glowId})`}
          style={{ transition: 'stroke-dashoffset 1.2s cubic-bezier(.4,0,.2,1)' }}
        />

        <text x="100" y="90" textAnchor="middle" fill="var(--text-primary)" fontSize="40" fontFamily="DM Mono" fontWeight="500">
          {display}
        </text>
        <text x="100" y="114" textAnchor="middle" fill="var(--text-muted)" fontSize="9.5" fontFamily="DM Mono" fontWeight="300" letterSpacing="0.12em">
          SHORT SCORE
        </text>
        <text x="100" y="128" textAnchor="middle" fill="var(--text-muted)" fontSize="8.5" fontFamily="DM Mono" fontWeight="300">
          / 100
        </text>
      </svg>
    </div>
  );
}
