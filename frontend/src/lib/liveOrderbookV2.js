/**
 * Isolated WebSocket feed for `/fokker-planck-v2` side-by-side comparison.
 * Leaves `liveOrderbook.js` (Model v1) 100% untouched.
 *
 * From each single Binance `@depth20@1000ms` + `@aggTrade` packet stream,
 * computes BOTH:
 *   - unweighted top-20 depth & cancel volumes (fed into untouched Model v1), and
 *   - 3-pillar distance-decayed depth, BBO Micro-Price quotes, far-wall depth,
 *     and rolling 15s realized priceChangeBps (fed into Model v2).
 */

const WS_BASE = "wss://stream.binance.com:9443/stream";
const SYMBOL_RE = /^[A-Z0-9]{2,20}$/;
const WINDOW_MS = 15000;
const FAR_THRESHOLD = 0.005;

function clamp(val, lo, hi) {
  return Math.min(hi, Math.max(lo, val));
}

function adaptiveDecayLambda(sigmaPerSec, horizonSec = 30) {
  const sigmaH = Math.max(1e-5, sigmaPerSec || 0.0006) * Math.sqrt(clamp(horizonSec, 1, 600));
  const charDist = clamp(0.55 * sigmaH, 0.00125, 0.00222);
  return 1.0 / charDist;
}

function cleanSide(raw, descending) {
  const out = [];
  if (!Array.isArray(raw)) return out;
  for (let i = 0; i < Math.min(raw.length, 50); i += 1) {
    const entry = raw[i];
    if (!Array.isArray(entry) || entry.length < 2) continue;
    const px = Number.parseFloat(entry[0]);
    const qty = Number.parseFloat(entry[1]);
    if (!Number.isFinite(px) || !Number.isFinite(qty) || px <= 0 || qty < 0) continue;
    out.push([px, qty]);
  }
  out.sort((a, b) => (descending ? b[0] - a[0] : a[0] - b[0]));
  return out;
}

function parseDualBook(rawBids, rawAsks, sigmaPerSec) {
  const bids = cleanSide(rawBids, true);
  const asks = cleanSide(rawAsks, false);
  if (bids.length === 0 || asks.length === 0) return null;

  const [bestBid, bestBidQty] = bids[0];
  const [bestAsk, bestAskQty] = asks[0];
  if (bestBid <= 0 || bestAsk < bestBid) return null;

  const midPrice = 0.5 * (bestBid + bestAsk);
  const spreadBps = midPrice > 0 ? ((bestAsk - bestBid) / midPrice) * 10000 : 0;
  const decayLambda = adaptiveDecayLambda(sigmaPerSec, 30);

  const accumulate = (levels) => {
    let nearWeighted = 0;
    let farRaw = 0;
    let rawTotal = 0;
    const weightedMap = new Map();
    const rawMap = new Map();
    for (const [px, qty] of levels) {
      const dist = Math.abs(px - midPrice) / midPrice;
      const w = Math.exp(-Math.min(60, decayLambda * dist));
      const wQty = qty * w;
      const key = px.toFixed(8);
      rawTotal += qty;
      rawMap.set(key, qty);
      weightedMap.set(key, wQty);
      if (dist >= FAR_THRESHOLD) farRaw += qty;
      else nearWeighted += wQty;
    }
    return { nearWeighted, farRaw, rawTotal, weightedMap, rawMap };
  };

  const b = accumulate(bids);
  const a = accumulate(asks);
  return {
    bestBid,
    bestAsk,
    bestBidQty,
    bestAskQty,
    midPrice,
    spreadBps,
    decayLambda,
    bidDepth: b.nearWeighted,
    askDepth: a.nearWeighted,
    farBidDepth: b.farRaw,
    farAskDepth: a.farRaw,
    rawBidForV1: b.rawTotal,
    rawAskForV1: a.rawTotal,
    wBidMap: b.weightedMap,
    wAskMap: a.weightedMap,
    rawBidMap: b.rawMap,
    rawAskMap: a.rawMap,
  };
}

function mapVanished(prevMap, curMap) {
  let vanished = 0;
  prevMap.forEach((oldQ, pxKey) => {
    const curQ = curMap.get(pxKey) || 0;
    if (oldQ > curQ) vanished += oldQ - curQ;
  });
  return vanished;
}

export function subscribeOrderbookStreamV2(symbol, onSnapshot, onStatus) {
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

  let prevBook = null;
  let latestBook = null;
  let tradesSinceLastBook = { buy: 0, sell: 0 };

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
    return clamp(Math.sqrt(Math.max(sumVar / count, 1e-10)), 0.00005, 0.015);
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
    let rawCancelBidForV1 = 0;
    let rawCancelAskForV1 = 0;
    for (const ev of cancelEvents) {
      canceledBidVol += ev.cBidW;
      canceledAskVol += ev.cAskW;
      rawCancelBidForV1 += ev.cBidR;
      rawCancelAskForV1 += ev.cAskR;
    }

    let priceChangeBps = null;
    let tradeWindowSec = 15;
    if (tradeEvents.length >= 3) {
      const p0 = tradeEvents[0].px;
      const p1 = tradeEvents[tradeEvents.length - 1].px;
      if (p0 > 0 && p1 > 0) priceChangeBps = Math.log(p1 / p0) * 10000;
      tradeWindowSec = Math.max(
        1,
        (tradeEvents[tradeEvents.length - 1].ts - tradeEvents[0].ts) / 1000
      );
    }

    onSnapshot?.({
      symbol: clean,
      ts: now,
      bestBid: latestBook.bestBid,
      bestAsk: latestBook.bestAsk,
      bestBidQty: latestBook.bestBidQty,
      bestAskQty: latestBook.bestAskQty,
      midPrice: latestBook.midPrice,
      spreadBps: latestBook.spreadBps,
      decayLambda: latestBook.decayLambda,
      bidDepth: latestBook.bidDepth,
      askDepth: latestBook.askDepth,
      farBidDepth: latestBook.farBidDepth,
      farAskDepth: latestBook.farAskDepth,
      rawBidForV1: latestBook.rawBidForV1,
      rawAskForV1: latestBook.rawAskForV1,
      takerBuyVol,
      takerSellVol,
      canceledBidVol,
      canceledAskVol,
      rawCancelBidForV1,
      rawCancelAskForV1,
      priceChangeBps,
      tradeWindowSec,
      sigmaPerSec: computeSigmaPerSec(),
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
        "Stream Binance tidak merespons dari jaringan ini. Gunakan Snapshot API atau preset."
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
        tradeEvents.push({ ts: now, buy, sell, px });
        tradesSinceLastBook.buy += buy;
        tradesSinceLastBook.sell += sell;
        secCloses.set(Math.floor(now / 1000), px);
        return;
      }

      if (stream.includes("@depth") || (Array.isArray(d?.bids) && Array.isArray(d?.asks))) {
        const book = parseDualBook(d.bids, d.asks, computeSigmaPerSec());
        if (!book) return;

        if (prevBook) {
          cancelEvents.push({
            ts: now,
            cBidW: Math.max(0, mapVanished(prevBook.wBidMap, book.wBidMap) - tradesSinceLastBook.sell),
            cAskW: Math.max(0, mapVanished(prevBook.wAskMap, book.wAskMap) - tradesSinceLastBook.buy),
            cBidR: Math.max(0, mapVanished(prevBook.rawBidMap, book.rawBidMap) - tradesSinceLastBook.sell),
            cAskR: Math.max(0, mapVanished(prevBook.rawAskMap, book.rawAskMap) - tradesSinceLastBook.buy),
          });
        }
        prevBook = book;
        tradesSinceLastBook = { buy: 0, sell: 0 };
        secCloses.set(Math.floor(now / 1000), book.midPrice);
        latestBook = book;
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
