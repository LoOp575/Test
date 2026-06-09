/* ============================================================
   API Route — /api/binance24hr

   Fetches Binance 24hr ticker data with multiple fallbacks.
   Some Binance hosts can fail depending on region/server/network.
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

function withTimeout(ms = 8000) {
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
    const timeout = withTimeout(8000);

    try {
      const response = await fetch(url, {
        signal: timeout.signal,
        headers: {
          accept: 'application/json',
          'user-agent': 'LQ-Short-Hunter/1.0'
        }
      });

      timeout.clear();

      if (!response.ok) {
        const preview = await response.text().catch(() => '');
        errors.push(`${url} -> HTTP ${response.status} ${preview.slice(0, 120)}`);
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

function normalizeTickerRows(raw) {
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
    .filter((item) => {
      return (
        Number.isFinite(item.lastPrice) &&
        item.lastPrice > 0 &&
        Number.isFinite(item.quoteVolume) &&
        item.quoteVolume > 0
      );
    })
    .sort((a, b) => b.quoteVolume - a.quoteVolume);
}

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    return res.status(405).json({
      ok: false,
      error: 'Method not allowed. Use GET.'
    });
  }

  try {
    let result = await fetchJsonWithFallback(SPOT_ENDPOINTS);
    let marketType = 'spot';

    if (!result.ok) {
      const futuresResult = await fetchJsonWithFallback(FUTURES_ENDPOINTS);

      if (futuresResult.ok) {
        result = futuresResult;
        marketType = 'futures';
      } else {
        return res.status(502).json({
          ok: false,
          error: 'Failed to fetch Binance ticker data from all fallback endpoints.',
          details: [...result.errors, ...futuresResult.errors].slice(0, 10)
        });
      }
    }

    const data = normalizeTickerRows(result.data);

    return res.status(200).json({
      ok: true,
      source: result.source,
      marketType,
      count: data.length,
      data
    });
  } catch (error) {
    console.error('[binance24hr] error:', error);

    return res.status(500).json({
      ok: false,
      error: 'Binance screener failed — ' + error.message
    });
  }
}
