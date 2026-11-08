import React from "react";
import { verdictClasses } from "../lib/format";

export default function VerdictBadge({ tone, label, size = "md" }) {
  const cls = verdictClasses(tone);
  const pad = size === "lg" ? "px-4 py-2 text-[12px]" : "";
  return (
    <span className={`${cls} ${pad}`}>
      <span className="verdict-dot" />
      {label}
    </span>
  );
}
