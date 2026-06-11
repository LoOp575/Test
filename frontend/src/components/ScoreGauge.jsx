import React, { useEffect, useId, useState } from "react";
import { clamp } from "../lib/utils";

export default function ScoreGauge({ score = 0, color = "#10b981", size = 200 }) {
  const safe = clamp(score, 0, 1);
  const [mounted, setMounted] = useState(false);
  const rawId = useId();
  const safeId = rawId.replace(/[^a-zA-Z0-9]/g, "");
  const gid = `grad-${safeId}`;
  const glid = `glow-${safeId}`;

  useEffect(() => {
    const id = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(id);
  }, []);

  const v = mounted ? safe : 0;

  const r = 82;
  const sw = 10;
  const c = 2 * Math.PI * r;
  const offset = c - v * c;
  const display = Math.round(safe * 100);

  return (
    <svg width={size} height={size} viewBox="0 0 200 200" data-testid="score-gauge">
      <defs>
        <linearGradient id={gid} x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor={color} stopOpacity="0.55" />
          <stop offset="100%" stopColor={color} />
        </linearGradient>
        <filter id={glid}>
          <feGaussianBlur stdDeviation="2.5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <circle cx="100" cy="100" r={r} fill="none" stroke="#27272A" strokeWidth={sw} />

      {/* Tick marks */}
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
            stroke="#27272A"
            strokeWidth="1"
          />
        );
      })}

      <circle
        cx="100"
        cy="100"
        r={r}
        fill="none"
        stroke={`url(#${gid})`}
        strokeWidth={sw}
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={offset}
        transform="rotate(-90 100 100)"
        filter={`url(#${glid})`}
        style={{ transition: "stroke-dashoffset 1.2s cubic-bezier(.4,0,.2,1)" }}
      />

      <text
        x="100"
        y="92"
        textAnchor="middle"
        fill="#FAFAFA"
        fontSize="42"
        fontFamily="JetBrains Mono"
        fontWeight="500"
      >
        {display}
      </text>
      <text
        x="100"
        y="114"
        textAnchor="middle"
        fill="#71717A"
        fontSize="9.5"
        fontFamily="JetBrains Mono"
        fontWeight="300"
        letterSpacing="2"
      >
        SHORT SCORE
      </text>
      <text
        x="100"
        y="128"
        textAnchor="middle"
        fill="#52525B"
        fontSize="8.5"
        fontFamily="JetBrains Mono"
        fontWeight="300"
      >
        / 100
      </text>
    </svg>
  );
}
