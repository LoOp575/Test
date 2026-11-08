import React from "react";

/**
 * Labeled 0-100 score bar.
 * tone: "pine" (demand quality), "gold" (signal), "red" (instability), "neutral"
 */
export default function ScoreBar({ label, value, tone = "pine", invertNote }) {
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  const fill =
    tone === "gold"
      ? "#B18A44"
      : tone === "red"
      ? "#B3402F"
      : tone === "neutral"
      ? "#9AA096"
      : "#144338";

  return (
    <div className="w-full">
      {label ? (
        <div className="flex items-baseline justify-between gap-3 mb-1.5">
          <span className="micro-label">{label}</span>
          <span className="font-mono text-[12px] font-semibold text-ink tabular-nums">
            {v.toFixed(0)}
            <span className="text-faint font-normal"> /100</span>
          </span>
        </div>
      ) : (
        <div className="flex items-center justify-end mb-1.5">
          <span className="font-mono text-[11px] font-semibold text-ink tabular-nums">{v.toFixed(0)}</span>
        </div>
      )}
      <div className="score-track" role="meter" aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100} aria-label={label || "score"}>
        <div className="score-fill" style={{ width: `${v}%`, backgroundColor: fill }} />
      </div>
      {invertNote && <p className="mt-1 text-[10px] text-muted">{invertNote}</p>}
    </div>
  );
}
