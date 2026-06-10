/* ============================================================
   AIXCHIA Agent Analysis API

   Server-only route. Never expose AIXCHIA_API_KEY to frontend.
   Supports common env names used in this project:
   - AIXCHIA_API_KEY or AIXCHIAAPIKEY
   - AIXCHIA_API_URL or AIXCHIAAPIURL
   - AIXCHIA_MODEL or AIXCHIAMODEL
   ============================================================ */

function getEnv(nameA, nameB, fallback = '') {
  return process.env[nameA] || process.env[nameB] || fallback;
}

function safeNumber(value, decimals = 4) {
  const num = Number(value);
  if (!Number.isFinite(num)) return 0;
  return Number(num.toFixed(decimals));
}

function compactPayload(body = {}) {
  const market = body.market || {};
  const autoLevels = body.autoLevels || {};
  const exhaustion = autoLevels.exhaustion || {};
  const results = body.results || {};

  return {
    token: market.symbol,
    market: {
      price: safeNumber(market.lastPrice, 10),
      change24hPercent: safeNumber(market.priceChangePercent, 4),
      quoteVolume: safeNumber(market.quoteVolume, 2),
      high24h: safeNumber(market.highPrice, 10),
      low24h: safeNumber(market.lowPrice, 10),
      volatility24h: safeNumber(market.volatility24h, 6)
    },
    pumpExhaustion: {
      phase: exhaustion.phase,
      score: safeNumber((exhaustion.exhaustionScore || 0) * 100, 2),
      pumpStrength: safeNumber((exhaustion.pumpStrength || 0) * 100, 2),
      highPressure: safeNumber((exhaustion.highPressure || 0) * 100, 2),
      volatilityStrength: safeNumber((exhaustion.volatilityStrength || 0) * 100, 2),
      positionInRange: safeNumber((exhaustion.positionInRange || 0) * 100, 2)
    },
    autoLevels: {
      takeProfit: safeNumber(autoLevels.takeProfit, 10),
      stopLoss: safeNumber(autoLevels.stopLoss, 10),
      riskReward: safeNumber(autoLevels.riskReward, 4),
      pullbackPct: safeNumber((autoLevels.pullbackPct || 0) * 100, 2),
      stopBufferPct: safeNumber((autoLevels.stopBufferPct || 0) * 100, 2)
    },
    monteCarlo: {
      score: results.score?.scorePercent,
      status: results.score?.status,
      probabilityDown: safeNumber((results.probabilities?.probDown || 0) * 100, 2),
      probabilityTP: safeNumber((results.probabilities?.probTP || 0) * 100, 2),
      probabilitySL: safeNumber((results.probabilities?.probSL || 0) * 100, 2),
      expectedValue: safeNumber(results.trade?.expectedValue, 10),
      expectedValuePct: safeNumber((results.trade?.expectedValuePct || 0) * 100, 4),
      riskReward: safeNumber(results.trade?.riskReward, 4),
      muAdjusted: safeNumber(results.liquidity?.muAdjusted, 6),
      liquidityPressure: safeNumber(results.liquidity?.liquidityPressure, 6),
      meanPrice: safeNumber(results.stats?.mean, 10),
      medianPrice: safeNumber(results.stats?.median, 10),
      worst5: safeNumber(results.stats?.worst5, 10),
      best5: safeNumber(results.stats?.best5, 10)
    }
  };
}

function fallbackAgent(payload) {
  const mc = payload.monteCarlo;
  const ex = payload.pumpExhaustion;

  const positives = [];
  const negatives = [];

  if (ex.score >= 70) positives.push(`Pump exhaustion tinggi (${ex.score}/100).`);
  else negatives.push(`Pump exhaustion belum ekstrem (${ex.score}/100).`);

  if (mc.probabilityDown >= 60) positives.push(`Probability Down mendukung (${mc.probabilityDown}%).`);
  else negatives.push(`Probability Down belum kuat (${mc.probabilityDown}%).`);

  if (mc.probabilityTP >= 50) positives.push(`Probability TP cukup baik (${mc.probabilityTP}%).`);
  else negatives.push(`Probability TP masih rendah (${mc.probabilityTP}%).`);

  if (mc.probabilitySL <= 30) positives.push(`Probability SL masih relatif terkontrol (${mc.probabilitySL}%).`);
  else negatives.push(`Probability SL masih tinggi (${mc.probabilitySL}%).`);

  if (mc.riskReward >= 1.5) positives.push(`Risk/Reward menarik (${mc.riskReward}).`);
  else negatives.push(`Risk/Reward belum ideal (${mc.riskReward}).`);

  return [
    `AI Agent fallback: ${payload.token} berada pada status ${mc.status} dengan score ${mc.score}/100.`,
    '',
    'Bacaan utama:',
    `- Phase: ${ex.phase}`,
    `- Auto TP: ${payload.autoLevels.takeProfit}`,
    `- Auto SL: ${payload.autoLevels.stopLoss}`,
    `- Probability TP/SL: ${mc.probabilityTP}% / ${mc.probabilitySL}%`,
    '',
    'Faktor pendukung:',
    ...(positives.length ? positives.map((x) => `- ${x}`) : ['- Belum ada faktor pendukung kuat.']),
    '',
    'Faktor risiko:',
    ...(negatives.length ? negatives.map((x) => `- ${x}`) : ['- Risiko utama tetap volatility dan invalidasi struktur.']),
    '',
    'Kesimpulan edukatif: gunakan hasil ini sebagai watchlist probabilitas, bukan sinyal pasti. Tunggu konfirmasi candle/struktur sebelum mengambil keputusan.'
  ].join('\n');
}

async function callAixchiaAgent(payload) {
  const apiKey = getEnv('AIXCHIA_API_KEY', 'AIXCHIAAPIKEY');
  const apiUrl = getEnv('AIXCHIA_API_URL', 'AIXCHIAAPIURL', 'https://www.aichixia.xyz/api/v1');
  const model = getEnv('AIXCHIA_MODEL', 'AIXCHIAMODEL', 'gpt-5-mini');

  if (!apiKey) {
    return {
      source: 'fallback',
      analysis: fallbackAgent(payload),
      warning: 'AIXCHIA API key belum tersedia di environment.'
    };
  }

  const endpoint = `${apiUrl.replace(/\/$/, '')}/chat/completions`;

  const systemPrompt = [
    'Kamu adalah market analysis agent untuk dashboard edukatif crypto.',
    'Tugasmu membaca data kuantitatif dari Pump Exhaustion, Auto TP/SL, dan Monte Carlo.',
    'Jangan memberi financial advice atau instruksi pasti entry.',
    'Jawab bahasa Indonesia santai tapi profesional.',
    'Fokus pada: kondisi token, alasan score, faktor pendukung, faktor risiko, dan apa yang perlu ditunggu sebagai konfirmasi.',
    'Jangan mengarang data di luar payload.'
  ].join(' ');

  const userPrompt = `Analisis payload token berikut secara ringkas dan berguna untuk trader edukatif. Berikan format:\n1. Ringkasan Kondisi\n2. Bacaan Score\n3. Peluang TP vs SL\n4. Risiko Utama\n5. Konfirmasi yang Perlu Ditunggu\n6. Kesimpulan Watch/No Trade\n\nPayload:\n${JSON.stringify(payload, null, 2)}`;

  const response = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`
    },
    body: JSON.stringify({
      model,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt }
      ],
      temperature: 0.25,
      max_tokens: 900
    })
  });

  const text = await response.text();
  let json = null;

  try {
    json = JSON.parse(text);
  } catch {
    json = null;
  }

  if (!response.ok) {
    return {
      source: 'fallback',
      analysis: fallbackAgent(payload),
      warning: `AIXCHIA request gagal (${response.status}).`,
      preview: text.slice(0, 240)
    };
  }

  const analysis =
    json?.choices?.[0]?.message?.content ||
    json?.choices?.[0]?.text ||
    json?.message ||
    json?.content ||
    '';

  if (!analysis) {
    return {
      source: 'fallback',
      analysis: fallbackAgent(payload),
      warning: 'AIXCHIA response tidak berisi content yang bisa dibaca.'
    };
  }

  return {
    source: 'aixchia',
    model,
    analysis
  };
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, error: 'Method not allowed' });
  }

  try {
    const payload = compactPayload(req.body || {});

    if (!payload.token) {
      return res.status(400).json({ ok: false, error: 'Missing token payload.' });
    }

    const agent = await callAixchiaAgent(payload);

    return res.status(200).json({
      ok: true,
      payload,
      ...agent
    });
  } catch (error) {
    return res.status(500).json({
      ok: false,
      error: error.message || 'Agent analysis failed.'
    });
  }
}
