/* ============================================================
   API Route — /api/binance24hr

   Fetches market screener data with strong fallbacks:
   1. Binance Spot
   2. Binance Futures
   3. CoinGecko public market data
   4. Local seed data so the UI never becomes empty
   ============================================================ */

const SPOT_ENDPOINTS = [
  'https://api.binance.com/api/v3/ticker/24hr',
  'https://api1.binance.com/api/v3/ticker/24hr',
  'https://api2.binance.com/api/v3/ticker/24hr',
  'https://api3.binance.com/api/v3/ticker/24hr',
  'https://api4.binance.com/api/v3/ticker/24hr'
];

const FUTURES_ENDPOINTS = [
  'https://fapi.binance.com/fapi/v1/ticker/24hr'
];

const COINGECKO_ENDPOINTS = [
  'https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1&sparkline=false&price_change_percentage=24h'
];

const SEED_MARKETS = [
  { symbol: 'BTCUSDT', lastPrice: 105000, priceChangePercent: 2.4, volume: 0, quoteVolume: 5000000000, highPrice: 108000, lowPrice: 101000 },
  { symbol: 'ETHUSDT', lastPrice: 3800, priceChangePercent: 3.8, volume: 0, quoteVolume: 2600000000, highPrice: 3920, lowPrice: 3600 },
  { symbol: 'SOLUSDT', lastPrice: 172, priceChangePercent: 8.7, volume: 0, quoteVolume: 1300000000, highPrice: 181, lowPrice: 154 },
  { symbol: 'BNBUSDT', lastPrice: 690, priceChangePercent: 1.9, volume: 0, quoteVolume: 850000000, highPrice: 705, lowPrice: 665 },
  { symbol: 'XRPUSDT', lastPrice: 2.25, priceChangePercent: 6.2, volume: 0, quoteVolume: 720000000, highPrice: 2.38, lowPrice: 2.07 },
  { symbol: 'DOGEUSDT', lastPrice: 0.19, priceChangePercent: 11.5, volume: 0, quoteVolume: 650000000, highPrice: 0.205, lowPrice: 0.165 },
  { symbol: 'PEPEUSDT', lastPrice: 0.000012, priceChangePercent: 18.4, volume: 0, quoteVolume: 610000000, highPrice: 0.0000135, lowPrice: 0.0000097 },
  { symbol: 'ADAUSDT', lastPrice: 0.72, priceChangePercent: 4.8, volume: 0, quoteVolume: 420000000, highPrice: 0.76, lowPrice: 0.68 },
  { symbol: 'AVAXUSDT', lastPrice: 38.5, priceChangePercent: 7.2, volume: 0, quoteVolume: 390000000, highPrice: 40.8, lowPrice: 35.4 },
  { symbol: 'LINKUSDT', lastPrice: 18.4, priceChangePercent: 5.1, volume: 0, quoteVolume: 310000000, highPrice: 19.1, lowPrice: 17.2 }
];

function withTimeout(ms = 7000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);

  return {
    signal: controller.signal,
    clear: () => clearTimeout(timer)
  };
}

async function fetchJsonWithFallback(urls) {
  const errors = [];

  for (const url of urls) {
    const timeout = withTimeout(7000);

    try {
      const response = await fetch(url, {
        signal: timeout.signal,
        headers: {
          accept: 'application/json',
          'user-agent': 'Mozilla/5.0 LQ-Short-Hunter/1.0'
        }
      });

      timeout.clear();

      if (!response.ok) {
        const preview = await response.text().catch(() => '');
        errors.push(`${url} -> HTTP ${response.status} ${preview.slice(0, 160)}`);
        continue;
      }

      const json = await response.json();

      if (!Array.isArray(json)) {
        errors.push(`${url} -> response is not an array`);
        continue;
      }

      return {
        ok: true,
        source: url,
        data: json
      };
    } catch (error) {
      timeout.clear();
      errors.push(`${url} -> ${error.name || 'Error'}: ${error.message}`);
    }
  }

  return {
    ok: false,
    errors
  };
}

function normalizeBinanceRows(raw) {
  return raw
    .filter((item) => item.symbol && item.symbol.endsWith('USDT'))
    .map((item) => {
      const lastPrice = Number(item.lastPrice);
      const priceChangePercent = Number(item.priceChangePercent);
      const volume = Number(item.volume);
      const quoteVolume = Number(item.quoteVolume);
      const highPrice = Number(item.highPrice);
      const lowPrice = Number(item.lowPrice);
      const volatility24h = lastPrice > 0 ? (highPrice - lowPrice) / lastPrice : 0;

      return {
        symbol: item.symbol,
        lastPrice,
        priceChangePercent,
        volume,
        quoteVolume,
        highPrice,
        lowPrice,
        volatility24h
      };
    })
    .filter(isValidMarketRow)
    .sort((a, b) => b.quoteVolume - a.quoteVolume);
}

function normalizeCoinGeckoRows(raw) {
  return raw
    .filter((item) => item.symbol && item.current_price)
    .map((item) => {
      const symbol = `${String(item.symbol).toUpperCase()}USDT`;
      const lastPrice = Number(item.current_price);
      const priceChangePercent = Number(item.price_change_percentage_24h || 0);
      const quoteVolume = Number(item.total_volume || 0);
      const highPrice = Number(item.high_24h || lastPrice);
      const lowPrice = Number(item.low_24h || lastPrice);
      const volatility24h = lastPrice > 0 ? (highPrice - lowPrice) / lastPrice : 0;

      return {
        symbol,
        lastPrice,
        priceChangePercent,
        volume: 0,
        quoteVolume,
        highPrice,
        lowPrice,
        volatility24h
      };
    })
    .filter(isValidMarketRow)
    .sort((a, b) => b.quoteVolume - a.quoteVolume);
}

function normalizeSeedRows(raw) {
  return raw
    .map((item) => {
      const lastPrice = Number(item.lastPrice);
      const highPrice = Number(item.highPrice);
      const lowPrice = Number(item.lowPrice);
      const volatility24h = lastPrice > 0 ? (highPrice - lowPrice) / lastPrice : 0;

      return {
        ...item,
        lastPrice,
        priceChangePercent: Number(item.priceChangePercent || 0),
        volume: Number(item.volume || 0),
        quoteVolume: Number(item.quoteVolume || 0),
        highPrice,
        lowPrice,
        volatility24h
      };
    })
    .filter(isValidMarketRow)
    .sort((a, b) => b.quoteVolume - a.quoteVolume);
}

function isValidMarketRow(item) {
  return (
    item &&
    item.symbol &&
    Number.isFinite(item.lastPrice) &&
    item.lastPrice > 0 &&
    Number.isFinite(item.quoteVolume) &&
    item.quoteVolume >= 0
  );
}

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    return res.status(405).json({
      ok: false,
      error: 'Method not allowed. Use GET.'
    });
  }

  const debugErrors = [];

  res.setHeader('Cache-Control', 'no-store, max-age=0');

  try {
    const spotResult = await fetchJsonWithFallback(SPOT_ENDPOINTS);

    if (spotResult.ok) {
      const data = normalizeBinanceRows(spotResult.data);
      if (data.length > 0) {
        return res.status(200).json({
          ok: true,
          source: spotResult.source,
          marketType: 'binance-spot',
          count: data.length,
          data
        });
      }
      debugErrors.push('Binance spot returned empty normalized data.');
    } else {
      debugErrors.push(...spotResult.errors);
    }

    const futuresResult = await fetchJsonWithFallback(FUTURES_ENDPOINTS);

    if (futuresResult.ok) {
      const data = normalizeBinanceRows(futuresResult.data);
      if (data.length > 0) {
        return res.status(200).json({
          ok: true,
          source: futuresResult.source,
          marketType: 'binance-futures',
          count: data.length,
          data,
          warning: 'Using Binance Futures fallback because Binance Spot failed.'
        });
      }
      debugErrors.push('Binance futures returned empty normalized data.');
    } else {
      debugErrors.push(...futuresResult.errors);
    }

    const geckoResult = await fetchJsonWithFallback(COINGECKO_ENDPOINTS);

    if (geckoResult.ok) {
      const data = normalizeCoinGeckoRows(geckoResult.data);
      if (data.length > 0) {
        return res.status(200).json({
          ok: true,
          source: geckoResult.source,
          marketType: 'coingecko-fallback',
          count: data.length,
          data,
          warning: 'Using CoinGecko fallback because Binance endpoints failed.',
          debug: debugErrors.slice(0, 8)
        });
      }
      debugErrors.push('CoinGecko returned empty normalized data.');
    } else {
      debugErrors.push(...geckoResult.errors);
    }

    const seedData = normalizeSeedRows(SEED_MARKETS);

    return res.status(200).json({
      ok: true,
      source: 'local-seed',
      marketType: 'local-seed-fallback',
      count: seedData.length,
      data: seedData,
      warning: 'External market APIs failed. Showing local seed data so the UI stays usable.',
      debug: debugErrors.slice(0, 12)
    });
  } catch (error) {
    console.error('[binance24hr] error:', error);

    const seedData = normalizeSeedRows(SEED_MARKETS);

    return res.status(200).json({
      ok: true,
      source: 'local-seed-after-error',
      marketType: 'local-seed-fallback',
      count: seedData.length,
      data: seedData,
      warning: 'Market API route crashed. Showing local seed data.',
      debug: [error.message]
    });
  }
}
