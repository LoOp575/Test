import React from "react";
import { statusInfo } from "../lib/utils";

export default function StatusBadge({ status = "NO_SHORT" }) {
  const info = statusInfo(status);
  return (
    <div
      className={"inline-flex items-center gap-2 px-3.5 py-2 rounded-sm border font-mono text-xs uppercase tracking-wider2 font-semibold badge-" + info.variant}
      data-testid="status-badge"
    >
      <span
        className="inline-block w-1.5 h-1.5 rounded-full animate-pulse-dot"
        style={{ backgroundColor: info.color }}
      />
      {info.label}
    </div>
  );
}
