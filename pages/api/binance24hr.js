/* ============================================================
   API Route — /api/binance24hr

   Fetches Binance 24hr ticker data from public Binance API.
   Used for the USDT market screener.
   ============================================================ */

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    return res.status(405).json({
      ok: false,
      error: 'Method not allowed. Use GET.'
    });
  }

  try {
    const response = await fetch('https://api4.binance.com/api/v3/ticker/24hr');

    if (!response.ok) {
      return res.status(response.status).json({
        ok: false,
        error: 'Failed to fetch Binance ticker data.'
      });
    }

    const raw = await response.json();

    const data = raw
      .filter((item) => item.symbol.endsWith('USDT'))
      .map((item) => {
        const lastPrice = Number(item.lastPrice);
        const priceChangePercent = Number(item.priceChangePercent);
        const volume = Number(item.volume);
        const quoteVolume = Number(item.quoteVolume);
        const highPrice = Number(item.highPrice);
        const lowPrice = Number(item.lowPrice);

        const volatility24h =
          lastPrice > 0 ? (highPrice - lowPrice) / lastPrice : 0;

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
      .filter((item) => Number.isFinite(item.lastPrice) && item.lastPrice > 0)
      .sort((a, b) => b.quoteVolume - a.quoteVolume);

    return res.status(200).json({
      ok: true,
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
