const usdCompact = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 2,
});

export function fmtPrice(v) {
  if (v == null || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  const digits = abs >= 100 ? 2 : abs >= 1 ? 3 : abs >= 0.01 ? 5 : 7;
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtUsdCompact(v) {
  if (v == null || Number.isNaN(v)) return "—";
  return `$${usdCompact.format(v)}`;
}

export function fmtPct(v, { signed = false, digits = 1 } = {}) {
  if (v == null || Number.isNaN(v)) return "—";
  const pct = v * 100;
  const sign = signed && pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(digits)}%`;
}

export function fmtMultiple(v, digits = 2) {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)}x`;
}

export function fmtProb(v) {
  if (v == null || Number.isNaN(v)) return "—";
  return `${Math.round(v * 100)}%`;
}

export function fmtDate(ms) {
  if (!ms) return "";
  return new Date(ms).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function verdictClasses(tone) {
  switch (tone) {
    case "long":
      return "verdict verdict-long";
    case "watch":
      return "verdict verdict-watch";
    case "neutral":
      return "verdict verdict-neutral";
    case "avoid":
      return "verdict verdict-avoid";
    default:
      return "verdict verdict-flat";
  }
}
