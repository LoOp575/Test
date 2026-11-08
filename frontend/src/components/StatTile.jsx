import React from "react";

export default function StatTile({ label, value, sub, tone = "ink" }) {
  const valueColor =
    tone === "pine"
      ? "text-pine-700"
      : tone === "gold"
      ? "text-gold-600"
      : tone === "red"
      ? "text-down"
      : "text-ink";
  return (
    <div className="stat-tile">
      <span className="micro-label">{label}</span>
      <span className={`font-display text-3xl font-semibold tracking-tight tabular-nums ${valueColor}`}>
        {value}
      </span>
      {sub && <span className="text-[11px] leading-4 text-muted">{sub}</span>}
    </div>
  );
}
