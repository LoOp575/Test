const API_BASE = process.env.REACT_APP_API_BASE || "";

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

export function fetchScreen(limit = 18) {
  return fetchJSON(`/api/screen?limit=${limit}`);
}

export function fetchSymbol(symbol) {
  return fetchJSON(`/api/screen/${encodeURIComponent(symbol)}`);
}
