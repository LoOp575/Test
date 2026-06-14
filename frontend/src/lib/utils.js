export const API_BASE = process.env.REACT_APP_BACKEND_URL || "";

export function clamp(value, min, max) {
  const n = Number(value);
  if (!Number.isFinite(n)) return min;
  return Math.min(Math.max(n, min), max);
}

export function toNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function formatLargeNumber(num, decimals = 2) {
  const n = Number(num);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function formatNumber(num, decimals = 2) {
  return formatLargeNumber(num, decimals);
}

export function formatCurrency(num) {
  const n = Number(num);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs > 0 && abs < 0.01) {
    return (
      "$" +
      n.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 8,
      })
    );
  }
  if (abs < 1) {
    return (
      "$" +
      n.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 4,
      })
    );
  }
  return "$" + formatLargeNumber(n, 2);
}

export function formatCompact(num) {
  const n = Number(num) || 0;
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return (n / 1_000_000_000).toFixed(2) + "B";
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (abs >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return n.toFixed(0);
}

export function formatPercent(num, decimals = 2) {
  const n = Number(num);
  if (!Number.isFinite(n)) return "—";
  return (n * 100).toFixed(decimals) + "%";
}

export function formatSignedPercent(num, decimals = 2) {
  const n = Number(num);
  if (!Number.isFinite(n)) return "—";
  const v = n.toFixed(decimals);
  return (n >= 0 ? "+" : "") + v + "%";
}

export function statusInfo(status) {
  const map = {
    STRONG_SHORT_SETUP: { label: "STRONG SHORT", variant: "success", color: "#10b981" },
    SHORT_VALID: { label: "SHORT VALID", variant: "success", color: "#10b981" },
    SHORT_WATCH: { label: "SHORT WATCH", variant: "warn", color: "#f59e0b" },
    WEAK_WATCH: { label: "WEAK WATCH", variant: "warn", color: "#f59e0b" },
    NO_SHORT: { label: "NO SHORT", variant: "neutral", color: "#71717a" },
    DANGER_STOP_RISK: { label: "DANGER · STOP RISK", variant: "danger", color: "#f43f5e" },
  };
  return map[status] || { label: status || "NO SHORT", variant: "neutral", color: "#71717a" };
}

export function phaseInfo(phase) {
  const map = {
    PUMP_EXHAUSTED: { label: "PUMP EXHAUSTED", variant: "danger" },
    PUMP_TIRED: { label: "PUMP TIRED", variant: "warn" },
    PUMP_WATCH: { label: "PUMP WATCH", variant: "info" },
    NORMAL: { label: "NORMAL", variant: "neutral" },
  };
  return map[phase] || { label: phase || "—", variant: "neutral" };
}

export function scoreColor(score01) {
  if (score01 >= 0.7) return "#10b981";
  if (score01 >= 0.4) return "#f59e0b";
  return "#f43f5e";
}

export async function apiGet(path) {
  const r = await fetch(`${API_BASE}${path}`);
  if (!r.ok) throw new Error(`GET ${path} failed: ${r.status}`);
  return r.json();
}

export async function apiPost(path, body) {
  const r = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) {
    let detail = `POST ${path} failed: ${r.status}`;
    try {
      const j = await r.json();
      detail = j.detail || j.error || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return r.json();
}
