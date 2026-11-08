/**
 * Browser-direct Binance public WebSocket feed (miniTicker).
 *
 * Why browser-direct: our backend is a stateless Vercel serverless function
 * with a 60s cap — it cannot hold a persistent socket open to Binance and fan
 * out to clients. The browser CAN reach wss://stream.binance.com:9443 directly
 * (public stream, no API key required), so live prices are a frontend concern.
 *
 * Deliberate degradation: if the socket cannot connect (region blocks, network,
 * Binance maintenance), consumers simply keep seeing the REST snapshot — the UI
 * never breaks, it just stops ticking.
 */

const WS_BASE = "wss://stream.binance.com:9443/stream";
const SYMBOL_RE = /^[A-Z0-9]{2,20}$/;

function toStreamName(symbol) {
  return `${symbol.toLowerCase()}@miniTicker`;
}

/**
 * Subscribe to miniTicker streams for a list of spot symbols (e.g. "BTCUSDT").
 * @param {string[]} symbols
 * @param {(tick: {symbol: string, price: number, changePercent: number, quoteVolume?: number}) => void} onTick
 * @param {(status: "live" | "offline") => void} [onStatus]
 * @returns {() => void} unsubscribe
 */
export function subscribeMiniTickers(symbols, onTick, onStatus) {
  const unique = Array.from(
    new Set((symbols || []).map((s) => String(s || "").toUpperCase()))
  ).filter((s) => SYMBOL_RE.test(s));

  if (unique.length === 0 || typeof WebSocket === "undefined") {
    return () => {};
  }

  const url = `${WS_BASE}?streams=${unique.map(toStreamName).join("/")}`;

  let ws = null;
  let closed = false;
  let retries = 0;
  let timer = null;

  const scheduleReconnect = () => {
    if (closed) return;
    const delay = Math.min(30000, 1500 * 2 ** retries);
    retries += 1;
    clearTimeout(timer);
    timer = setTimeout(connect, delay);
  };

  const connect = () => {
    if (closed) return;
    try {
      ws = new WebSocket(url);
    } catch {
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      retries = 0;
      onStatus?.("live");
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        const d = msg?.data ?? msg;
        if (!d || !d.s) return;
        const open = Number.parseFloat(d.o);
        const close = Number.parseFloat(d.c);
        if (!Number.isFinite(close)) return;
        const changePercent =
          Number.isFinite(open) && open > 0 ? ((close - open) / open) * 100 : 0;
        const quoteVolume = Number.parseFloat(d.q);
        onTick?.({
          symbol: String(d.s).toUpperCase(),
          price: close,
          changePercent,
          quoteVolume: Number.isFinite(quoteVolume) ? quoteVolume : undefined,
        });
      } catch {
        /* malformed frame — ignore */
      }
    };

    ws.onerror = () => {
      /* onclose fires next; reconnection is handled there */
    };

    ws.onclose = () => {
      onStatus?.("offline");
      scheduleReconnect();
    };
  };

  connect();

  return () => {
    closed = true;
    clearTimeout(timer);
    if (ws && ws.readyState <= WebSocket.OPEN) {
      try {
        ws.close();
      } catch {
        /* noop */
      }
    }
  };
}
