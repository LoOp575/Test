import React from "react";
import { clamp } from "../lib/utils";

const TONE = {
  up: { value: "text-emerald-400", bar: "bg-emerald-400" },
  down: { value: "text-rose-400", bar: "bg-rose-400" },
  warn: { value: "text-amber-400", bar: "bg-amber-400" },
  info: { value: "text-blue-400", bar: "bg-blue-400" },
  neutral: { value: "text-ink-50", bar: "bg-ink-300" },
};

export default function MetricCard({ label, value, progress, tone = "neutral", testid }) {
  const t = TONE[tone] || TONE.neutral;
  const hasProg = progress !== undefined && progress !== null;
  const p = hasProg ? clamp(Number(progress), 0, 1) : 0;
  return (
    <div className="panel-elevated p-3.5" data-testid={testid}>
      <div className="font-mono text-[9px] uppercase tracking-wider2 text-ink-300 mb-1.5">
        {label}
      </div>
      <div className={"font-mono text-base font-semibold tabular-nums break-all " + t.value}>
        {value}
      </div>
      {hasProg && (
        <div className="mt-2 h-0.5 bg-ink-600 rounded-sm overflow-hidden">
          <div
            className={"h-full transition-all duration-700 " + t.bar}
            style={{ width: p * 100 + "%" }}
          />
        </div>
      )}
    </div>
  );
}
