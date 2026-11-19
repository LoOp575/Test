/**
 * Browser-direct Binance public WebSocket feed for Fokker–Planck 3-pillar
 * anti-spoofing microstructure analysis (`@depth20@1000ms` + `@aggTrade`).
 *
 * Computes client-side before sending to `/api/fokker-planck/simulate`:
 *   1. Real taker-buy / taker-sell volume + rolling 15s priceChangeBps (iceberg guard)
 *   2. BBO quotes & quantities (`bestBid`, `bestAsk`, `bestBidQty`, `bestAskQty`) + live Micro-Price
 *   3. Volatility-adaptive Exponential Distance Decay `w(d) = exp(-λ·d)` separating
 *      near-touch weighted liquidity from distant walls (`d >= 0.5%`).
 *
 * Uses the same public `wss://stream.binance.com:9443/stream` endpoint already
 * used in `liveTicker.js` (no API key, compatible with stateless Vercel deploys).
 * Degrades cleanly if the browser cannot reach Binance (offline / geo-block):
 * invokes `onStatus("offline", reason)` and never throws.
 */

const WS_BASE = "wss://stream.binance.com:9443/stream";
const SYMBOL_RE = /^[A-Z0-9]{2,20}$/;
const WINDOW_MS = 15000;
const FAR_THRESHOLD = 0.005; // 0.5% from mid-price

function clamp(val, lo, hi) {
  return Math.min(hi, Math.max(lo, val));
}

function adaptiveDecayLambda(sigmaPerSec, horizonSec = 30) {
  const sigmaH = Math.max(1e-5, sigmaPerSec || 0.0006) * Math.sqrt(clamp(horizonSec, 1, 600));
  const charDist = clamp(0.55 * sigmaH, 0.00125, 0.00222); // λ ∈ [450, 800]
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

function parseWeightedBook(rawBids, rawAsks, sigmaPerSec) {
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
    for (const [px, qty] of levels) {
      const dist = Math.abs(px - midPrice) / midPrice;
      const w = Math.exp(-Math.min(60, decayLambda * dist));
      const wQty = qty * w;
      rawTotal += qty;
      if (dist >= FAR_THRESHOLD) {
        farRaw += qty;
      } else {
        nearWeighted += wQty;
      }
      weightedMap.set(px.toFixed(8), wQty);
    }
    return { nearWeighted, farRaw, rawTotal, weightedMap };
  };

  const b = accumulate(bids);
  const a = accumulate(asks);
  const bboSum = bestBidQty + bestAskQty;
  const bboImb = bboSum > 1e-12 ? bestBidQty / bboSum : 0.5;
  const microPrice = bestBid * (1 - bboImb) + bestAsk * bboImb;

  return {
    bestBid,
    bestAsk,
    bestBidQty,
    bestAskQty,
    midPrice,
    microPrice,
    spreadBps,
    decayLambda,
    bidDepth: b.nearWeighted,
    askDepth: a.nearWeighted,
    farBidDepth: b.farRaw,
    farAskDepth: a.farRaw,
    rawBidDepth: b.rawTotal,
    rawAskDepth: a.rawTotal,
    bidMap: b.weightedMap,
    askMap: a.weightedMap,
  };
}

/**
 * Subscribe to top-20 order book + aggregate trades for a single USDT pair.
 * Emits a rolling 15-second 3-pillar microstructure summary every ~1.2 seconds.
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

  // Rolling windows: { ts, buy, sell, px } and { ts, cBid, cAsk }
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
    return clamp(raw, 0.00005, 0.015);
  };

  const computePriceChangeBps = () => {
    if (tradeEvents.length < 3) return null;
    const firstPx = tradeEvents[0].px;
    const lastPx = tradeEvents[tradeEvents.length - 1].px;
    if (!(firstPx > 0 && lastPx > 0)) return null;
    return Math.log(lastPx / firstPx) * 10000;
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
    const priceChangeBps = computePriceChangeBps();
    const spanSec =
      tradeEvents.length >= 2
        ? Math.max(1, (tradeEvents[tradeEvents.length - 1].ts - tradeEvents[0].ts) / 1000)
        : 15;

    onSnapshot?.({
      symbol: clean,
      ts: now,
      bestBid: latestBook.bestBid,
      bestAsk: latestBook.bestAsk,
      bestBidQty: latestBook.bestBidQty,
      bestAskQty: latestBook.bestAskQty,
      midPrice: latestBook.midPrice,
      microPrice: latestBook.microPrice,
      spreadBps: latestBook.spreadBps,
      decayLambda: latestBook.decayLambda,
      bidDepth: latestBook.bidDepth,
      askDepth: latestBook.askDepth,
      farBidDepth: latestBook.farBidDepth,
      farAskDepth: latestBook.farAskDepth,
      rawBidDepth: latestBook.rawBidDepth,
      rawAskDepth: latestBook.rawAskDepth,
      takerBuyVol,
      takerSellVol,
      canceledBidVol,
      canceledAskVol,
      priceChangeBps,
      tradeWindowSec: spanSec,
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
        tradeEvents.push({ ts: now, buy, sell, px });
        tradesSinceLastBook.buy += buy;
        tradesSinceLastBook.sell += sell;
        secCloses.set(Math.floor(now / 1000), px);
        return;
      }

      if (stream.includes("@depth") || (Array.isArray(d?.bids) && Array.isArray(d?.asks))) {
        const sigmaEst = computeSigmaPerSec();
        const book = parseWeightedBook(d.bids, d.asks, sigmaEst);
        if (!book) return;

        if (prevBids && prevAsks) {
          let bidVanished = 0;
          let askVanished = 0;
          prevBids.forEach((oldQ, pxKey) => {
            const curQ = book.bidMap.get(pxKey) || 0;
            if (oldQ > curQ) bidVanished += oldQ - curQ;
          });
          prevAsks.forEach((oldQ, pxKey) => {
            const curQ = book.askMap.get(pxKey) || 0;
            if (oldQ > curQ) askVanished += oldQ - curQ;
          });
          // Subtract real executed aggressor trades; remainder is distance-weighted cancel
          const cBid = Math.max(0, bidVanished - tradesSinceLastBook.sell);
          const cAsk = Math.max(0, askVanished - tradesSinceLastBook.buy);
          cancelEvents.push({ ts: now, cBid, cAsk });
        }

        prevBids = book.bidMap;
        prevAsks = book.askMap;
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
