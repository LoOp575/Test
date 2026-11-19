/**
 * Browser-direct Binance public WebSocket feed for Fokker–Planck microstructure
 * analysis (`@depth20@1000ms` + `@aggTrade`).
 *
 * Uses the same public `wss://stream.binance.com:9443/stream` endpoint already
 * used in `liveTicker.js` (no API key, compatible with stateless Vercel deploys).
 * Degrades cleanly if the browser cannot reach Binance (offline / geo-block):
 * invokes `onStatus("offline", reason)` and never throws.
 */

const WS_BASE = "wss://stream.binance.com:9443/stream";
const SYMBOL_RE = /^[A-Z0-9]{2,20}$/;
const WINDOW_MS = 15000;

function parseLevels(levels) {
  let total = 0;
  let best = 0;
  const map = new Map();
  if (!Array.isArray(levels)) return { total, best, map };
  for (let i = 0; i < Math.min(levels.length, 20); i += 1) {
    const entry = levels[i];
    if (!Array.isArray(entry) || entry.length < 2) continue;
    const px = Number.parseFloat(entry[0]);
    const qty = Number.parseFloat(entry[1]);
    if (!Number.isFinite(px) || !Number.isFinite(qty) || px <= 0 || qty < 0) continue;
    if (i === 0) best = px;
    total += qty;
    map.set(px.toFixed(8), qty);
  }
  return { total, best, map };
}

/**
 * Subscribe to top-20 order book + aggregate trades for a single USDT pair.
 * Emits a rolling 15-second microstructure summary every ~1.2 seconds.
 */
export function subscribeOrderbookStream(symbol, onSnapshot, onStatus) {
  const clean = String(symbol || "").toUpperCase().trim();
  if (!SYMBOL_RE.test(clean) || typeof WebSocket === "undefined") {
    onStatus?.("offline", "Simbol atau WebSocket browser tidak tersedia.");
    return () => {};
  }

  const lower = clean.toLowerCase();
  const url = `${WS_BASE}?streams=${lower}@depth20@1000ms/${lower}@aggTrade`;

  let ws = null;
  let closed = false;
  let emitTimer = null;
  let connectTimeout = null;

  let prevBids = null;
  let prevAsks = null;
  let latestBook = null;
  let tradesSinceLastBook = { buy: 0, sell: 0 };

  // Rolling windows: { ts, buy, sell, cBid, cAsk, price }
  const tradeEvents = [];
  const cancelEvents = [];
  const secCloses = new Map();

  const prune = (now) => {
    const cutoff = now - WINDOW_MS;
    while (tradeEvents.length > 0 && tradeEvents[0].ts < cutoff) tradeEvents.shift();
    while (cancelEvents.length > 0 && cancelEvents[0].ts < cutoff) cancelEvents.shift();
    const secCutoff = Math.floor(cutoff / 1000) - 2;
    for (const sec of secCloses.keys()) {
      if (sec < secCutoff) secCloses.delete(sec);
    }
  };

  const computeSigmaPerSec = () => {
    const entries = Array.from(secCloses.entries()).sort((a, b) => a[0] - b[0]);
    if (entries.length < 3) return 0.0006;
    let sumVar = 0;
    let count = 0;
    for (let i = 1; i < entries.length; i += 1) {
      const dt = Math.max(1, entries[i][0] - entries[i - 1][0]);
      const p0 = entries[i - 1][1];
      const p1 = entries[i][1];
      if (p0 > 0 && p1 > 0) {
        const lr = Math.log(p1 / p0);
        sumVar += (lr * lr) / dt;
        count += 1;
      }
    }
    if (count === 0) return 0.0006;
    const raw = Math.sqrt(Math.max(sumVar / count, 1e-10));
    return Math.min(0.015, Math.max(0.00005, raw));
  };

  const emit = () => {
    if (closed || !latestBook) return;
    const now = Date.now();
    prune(now);

    let takerBuyVol = 0;
    let takerSellVol = 0;
    for (const ev of tradeEvents) {
      takerBuyVol += ev.buy;
      takerSellVol += ev.sell;
    }

    let canceledBidVol = 0;
    let canceledAskVol = 0;
    for (const ev of cancelEvents) {
      canceledBidVol += ev.cBid;
      canceledAskVol += ev.cAsk;
    }

    const sigmaPerSec = computeSigmaPerSec();
    onSnapshot?.({
      symbol: clean,
      ts: now,
      midPrice: latestBook.midPrice,
      spreadBps: latestBook.spreadBps,
      bidDepth: latestBook.bidDepth,
      askDepth: latestBook.askDepth,
      takerBuyVol,
      takerSellVol,
      canceledBidVol,
      canceledAskVol,
      sigmaPerSec,
      samples: tradeEvents.length,
    });
  };

  try {
    onStatus?.("connecting");
    ws = new WebSocket(url);
  } catch {
    onStatus?.("offline", "Gagal membuka koneksi WebSocket ke Binance.");
    return () => {};
  }

  connectTimeout = setTimeout(() => {
    if (ws && ws.readyState !== WebSocket.OPEN) {
      try {
        ws.close();
      } catch {
        /* noop */
      }
      onStatus?.(
        "offline",
        "Stream Binance tidak merespons (kemungkinan diblokir jaringan/region). Gunakan snapshot REST atau preset."
      );
    }
  }, 7000);

  ws.onopen = () => {
    clearTimeout(connectTimeout);
    onStatus?.("live");
    emitTimer = setInterval(emit, 1200);
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      const stream = String(msg?.stream || "");
      const d = msg?.data ?? msg;
      const now = Date.now();

      if (stream.includes("@aggTrade") || d?.e === "aggTrade") {
        const px = Number.parseFloat(d.p);
        const qty = Number.parseFloat(d.q);
        const isBuyerMaker = Boolean(d.m);
        if (!Number.isFinite(px) || !Number.isFinite(qty) || px <= 0 || qty <= 0) return;
        const buy = isBuyerMaker ? 0 : qty;
        const sell = isBuyerMaker ? qty : 0;
        tradeEvents.push({ ts: now, buy, sell });
        tradesSinceLastBook.buy += buy;
        tradesSinceLastBook.sell += sell;
        secCloses.set(Math.floor(now / 1000), px);
        return;
      }

      if (stream.includes("@depth") || (Array.isArray(d?.bids) && Array.isArray(d?.asks))) {
        const bids = parseLevels(d.bids);
        const asks = parseLevels(d.asks);
        if (bids.best <= 0 || asks.best <= 0) return;

        if (prevBids && prevAsks) {
          let bidVanished = 0;
          let askVanished = 0;
          prevBids.forEach((oldQ, pxKey) => {
            const curQ = bids.map.get(pxKey) || 0;
            if (oldQ > curQ) bidVanished += oldQ - curQ;
          });
          prevAsks.forEach((oldQ, pxKey) => {
            const curQ = asks.map.get(pxKey) || 0;
            if (oldQ > curQ) askVanished += oldQ - curQ;
          });
          const cBid = Math.max(0, bidVanished - tradesSinceLastBook.sell);
          const cAsk = Math.max(0, askVanished - tradesSinceLastBook.buy);
          cancelEvents.push({ ts: now, cBid, cAsk });
        }

        prevBids = bids.map;
        prevAsks = asks.map;
        tradesSinceLastBook = { buy: 0, sell: 0 };

        const midPrice = 0.5 * (bids.best + asks.best);
        const spreadBps = midPrice > 0 ? ((asks.best - bids.best) / midPrice) * 10000 : 0;
        secCloses.set(Math.floor(now / 1000), midPrice);
        latestBook = {
          midPrice,
          spreadBps,
          bidDepth: bids.total,
          askDepth: asks.total,
        };
      }
    } catch {
      /* ignore malformed frame */
    }
  };

  ws.onerror = () => {
    /* handled in onclose */
  };

  ws.onclose = () => {
    clearTimeout(connectTimeout);
    clearInterval(emitTimer);
    if (!closed) {
      onStatus?.(
        "offline",
        "Koneksi WebSocket Binance terputus atau tidak terjangkau dari jaringan ini."
      );
    }
  };

  return () => {
    closed = true;
    clearTimeout(connectTimeout);
    clearInterval(emitTimer);
    if (ws && ws.readyState <= WebSocket.OPEN) {
      try {
        ws.close();
      } catch {
        /* noop */
      }
    }
  };
}
