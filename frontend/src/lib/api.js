const API_BASE = process.env.REACT_APP_API_BASE || "";

async function postJSON(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  let payload = null;
  try {
    payload = await res.json();
  } catch {
    /* fall through */
  }
  if (!res.ok) {
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.errors?.join(", ") || `Request failed (HTTP ${res.status})`;
    throw new Error(message);
  }
  return payload;
}

async function fetchJSON(path) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { accept: "application/json" },
  });
  let payload = null;
  try {
    payload = await res.json();
  } catch {
    /* fall through */
  }
  if (!res.ok) {
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.errors?.join(", ") || `Request failed (HTTP ${res.status})`;
    throw new Error(message);
  }
  return payload;
}

export function fetchScreen(limit = 18, minQuote) {
  const q = minQuote ? `&minQuote=${encodeURIComponent(minQuote)}` : "";
  return fetchJSON(`/api/screen?limit=${limit}${q}`);
}

export function fetchSymbol(symbol) {
  return fetchJSON(`/api/screen/${encodeURIComponent(symbol)}`);
}

export function runCalibration({ lookbackDays = 730, stepDays = 7, label = "pos" } = {}) {
  return postJSON("/api/calibrate/run", { lookbackDays, stepDays, label });
}

export function runCalibrationSweep({ lookbackDays = 730, stepDays = 7 } = {}) {
  return postJSON("/api/calibrate/sweep", { lookbackDays, stepDays, label: "pos" });
}

export function fetchCalibrationFormulas() {
  return fetchJSON("/api/calibrate/formulas");
}

export function fetchCalibrationHistory(limit = 25) {
  return fetchJSON(`/api/calibrate/history?limit=${limit}`);
}
