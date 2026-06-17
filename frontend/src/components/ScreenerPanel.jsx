import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Search,
  RotateCcw,
  ArrowUpDown,
  Flame,
  Sparkles,
  AlertCircle,
  ShieldAlert,
} from "lucide-react";
import {
  apiGet,
  formatCompact,
  formatCurrency,
  formatPercent,
  formatSignedPercent,
} from "../lib/utils";

const TABS = [
  { key: "shortCandidates", label: "Short Candidates", icon: Flame },
  { key: "trending", label: "Trending", icon: Sparkles },
  { key: "gainers", label: "Gainers" },
  { key: "losers", label: "Losers" },
  { key: "quoteVolume", label: "Volume" },
  { key: "volatility", label: "Volatility" },
];

function clamp(v, lo = 0, hi = 1) {
  return Math.max(lo, Math.min(hi, Number.isFinite(v) ? v : 0));
}

function normRank(v, max) {
  return !max || max <= 0 ? 0 : clamp(v / max);
}

function calcShortPipeline(row, ranks) {
  const price = Number(row.lastPrice || 0);
  const high = Math.max(Number(row.highPrice || price), price);
  const low = Math.min(Number(row.lowPrice || price), price);
  const range = Math.max(high - low, 0);
  const rangePct = price > 0 ? range / price : Number(row.volatility24h || 0);
  const changePct = Number(row.priceChangePercent || 0);
  const volumeRank = normRank(row.quoteVolume || 0, ranks.maxVolume);
  const absMoveRank = normRank(Math.abs(changePct), ranks.maxAbsChange);
  const volRank = normRank(row.volatility24h || rangePct || 0, ranks.maxVolatility);

  const positionInRange = range > 0 ? clamp((price - low) / range) : 0.5;
  const upperRejection = range > 0 ? clamp((high - price) / range) : 0;
  const pumpScore = clamp(Math.max(changePct, 0) / 30);
  const rangeTopScore = clamp((positionInRange - 0.55) / 0.45);
  const volatilityScore = clamp((row.volatility24h || rangePct || 0) / 0.35);
  const liquidityScore = volumeRank;

  // Dashboard list does not always have fresh 3h candles, so this is a conservative radar proxy.
  // Full analyze page still calculates real 1h kline fuel metrics.
  const fuelWeaknessScore = clamp(
    0.34 * upperRejection +
    0.28 * volatilityScore +
    0.20 * pumpScore +
    0.18 * clamp(1 - liquidityScore)
  );
  const confirmationScore = clamp(
    0.44 * upperRejection +
    0.26 * volatilityScore +
    0.18 * rangeTopScore +
    0.12 * absMoveRank
  );
  const stopHuntRisk = clamp(
    0.34 * rangeTopScore +
    0.30 * pumpScore +
    0.26 * volatilityScore +
    0.10 * liquidityScore -
    0.18 * upperRejection
  );
  const monteEdgeProxy = clamp(
    0.40 * fuelWeaknessScore +
    0.25 * confirmationScore +
    0.20 * volatilityScore +
    0.15 * pumpScore -
    0.25 * stopHuntRisk
  );

  const pumpGate = changePct >= 5 ? 1 : changePct > 0 ? 0.35 : 0;
  const rangeGate = positionInRange >= 0.55 ? 1 : 0.55;
  let score = clamp(
    0.25 * pumpScore +
    0.30 * fuelWeaknessScore +
    0.25 * confirmationScore +
    0.20 * monteEdgeProxy -
    0.22 * stopHuntRisk
  );
  score = clamp(score * pumpGate * rangeGate);

  let stage = "No Short";
  let tone = "bg-ink-600 text-ink-200 border-ink-500";
  let reason = "Belum pump kuat, tidak prioritas short.";

  if (changePct > 0 && score < 0.28) {
    stage = "Hot Pump Watch";
    tone = "bg-sky-400/10 text-sky-300 border-sky-400/20";
    reason = "Pump terdeteksi, tapi fuel lemah/rejection belum cukup jelas.";
  }
  if (score >= 0.28) {
    stage = "Fuel Weakening";
    tone = "bg-amber-400/10 text-amber-300 border-amber-400/20";
    reason = "Pump mulai lelah secara proxy; perlu cek detail 3h di halaman analyze.";
  }
  if (score >= 0.46 && fuelWeaknessScore >= 0.48) {
    stage = "Exhaustion Candidate";
    tone = "bg-orange-400/10 text-orange-300 border-orange-400/20";
    reason = "Pump + volatilitas/rejection mulai mendukung exhaustion.";
  }
  if (score >= 0.58 && confirmationScore >= 0.52 && stopHuntRisk < 0.62) {
    stage = "Short Valid After Confirmation";
    tone = "bg-emerald-400/10 text-emerald-300 border-emerald-400/20";
    reason = "Masuk kandidat valid, tetap tunggu rejection/lower-high/break support.";
  }
  if (changePct > 8 && stopHuntRisk >= 0.66) {
    stage = "Danger Stop Hunt";
    tone = "bg-rose-400/10 text-rose-300 border-rose-400/20";
    reason = "Pump masih rawan final spike; jangan anggap score sebagai entry langsung.";
    score = Math.min(score, 0.55);
  }

  return {
    pipelineScore: score,
    pumpScore,
    fuelWeaknessScore,
    confirmationScore,
    stopHuntRisk,
    positionInRange,
    upperRejection,
    stage,
    tone,
    reason,
  };
}

function enrich(rows) {
  const safeRows = Array.isArray(rows) ? rows : [];
  const maxVolume = Math.max(...safeRows.map((r) => r.quoteVolume || 0), 1);
  const maxAbsChange = Math.max(...safeRows.map((r) => Math.abs(r.priceChangePercent || 0)), 1);
  const maxVolatility = Math.max(...safeRows.map((r) => r.volatility24h || 0), 1);
  const ranks = { maxVolume, maxAbsChange, maxVolatility };

  return safeRows.map((r) => {
    const volS = normRank(r.quoteVolume || 0, maxVolume);
    const chS = normRank(Math.abs(r.priceChangePercent || 0), maxAbsChange);
    const vlS = normRank(r.volatility24h || 0, maxVolatility);
    const pipeline = calcShortPipeline(r, ranks);
    return {
      ...r,
      ...pipeline,
      trendingScore: r.trendingScore ?? (0.4 * volS + 0.3 * chS + 0.3 * vlS),
      shortCandidateScore: pipeline.pipelineScore,
    };
  });
}

export default function ScreenerPanel() {
  const [rows, setRows] = useState([]);
  const [trendRows, setTrendRows] = useState([]);
  const [meta, setMeta] = useState({});
  const [trendMeta, setTrendMeta] = useState({});
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("shortCandidates");
  const [loading, setLoading] = useState(false);
  const [trendLoading, setTrendLoading] = useState(false);
  const [err, setErr] = useState(null);
  const nav = useNavigate();

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      const j = await apiGet("/api/markets");
      setRows(enrich(j.data || []));
      setMeta({ source: j.source, count: j.count, warning: j.warning });
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadTrending() {
    setTrendLoading(true);
    setErr(null);
    try {
      const j = await apiGet("/api/trending");
      setTrendRows(enrich(j.data || []));
      setTrendMeta({ source: j.source, count: j.count, warning: j.warning });
    } catch (e) {
      setErr(e.message);
    } finally {
      setTrendLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (sort === "trending" && trendRows.length === 0 && !trendLoading) {
      loadTrending();
    }
  }, [sort, trendRows.length, trendLoading]);

  const activeRows = sort === "trending" && trendRows.length > 0 ? trendRows : rows;
  const activeMeta = sort === "trending" && trendRows.length > 0 ? trendMeta : meta;
  const isLoading = loading || (sort === "trending" && trendLoading);

  const filtered = useMemo(() => {
    const q = query.trim().toUpperCase();
    let out = activeRows.filter((r) => (q ? r.symbol.includes(q) : true));
    out = [...out].sort((a, b) => {
      if (sort === "shortCandidates") return b.shortCandidateScore - a.shortCandidateScore;
      if (sort === "trending") return b.trendingScore - a.trendingScore;
      if (sort === "gainers") return (b.priceChangePercent || 0) - (a.priceChangePercent || 0);
      if (sort === "losers") return (a.priceChangePercent || 0) - (b.priceChangePercent || 0);
      if (sort === "volatility") return (b.volatility24h || 0) - (a.volatility24h || 0);
      return (b.quoteVolume || 0) - (a.quoteVolume || 0);
    });
    return out.slice(0, 100);
  }, [activeRows, query, sort]);

  const openToken = (sym) => nav(`/analyze/${encodeURIComponent(sym)}`);

  function handleTab(key) {
    setSort(key);
    if (key === "trending" && trendRows.length === 0) {
      loadTrending();
    }
  }

  function refreshActive() {
    if (sort === "trending") return loadTrending();
    return load();
  }

  return (
    <div className="panel p-4 sm:p-6 animate-fade-in" data-testid="screener-panel">
      <div className="flex items-end justify-between gap-3 mb-4 flex-wrap">
        <div>
          <div className="data-label mb-1">// Token Screener</div>
          <h2 className="font-display text-2xl tracking-tight text-ink-50">
            Pump Exhaustion Radar
          </h2>
          <p className="text-xs text-ink-300 mt-1 max-w-2xl">
            Short Candidates sekarang pakai pipeline: pump → fuel weakening → exhaustion → confirmation → stop-hunt filter.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className="font-mono text-[10px] uppercase tracking-wider2 text-ink-300"
            data-testid="screener-source"
          >
            src: <span className="text-ink-100">{activeMeta.source || "—"}</span>
            {activeMeta.count != null && (
              <>
                {" · "}rows: <span className="text-ink-100">{activeMeta.count}</span>
              </>
            )}
          </span>
          <button
            type="button"
            className="btn-ghost"
            onClick={refreshActive}
            disabled={isLoading}
            data-testid="refresh-button"
          >
            <RotateCcw size={13} strokeWidth={1.5} className={isLoading ? "animate-spin" : ""} />
            {isLoading ? "Loading" : "Refresh"}
          </button>
        </div>
      </div>

      {sort === "shortCandidates" && (
        <div className="mb-3 px-3 py-2 text-xs text-ink-300 bg-ink-700/40 border border-ink-600 rounded-sm flex items-start gap-2">
          <ShieldAlert size={14} strokeWidth={1.5} className="mt-0.5 flex-shrink-0 text-amber-300" />
          <span>
            Kandidat di sini adalah radar awal berbasis data list 24h. Klik token untuk validasi penuh memakai kline 1H, fuel metrics, Monte Carlo TP/SL, dan AI Market Fuel Checker.
          </span>
        </div>
      )}

      {activeMeta.warning && (
        <div className="mb-3 px-3 py-2 text-xs text-amber-400 bg-amber-400/5 border border-amber-400/20 rounded-sm flex items-start gap-2">
          <AlertCircle size={14} strokeWidth={1.5} className="mt-0.5 flex-shrink-0" />
          <span>{activeMeta.warning}</span>
        </div>
      )}
      {err && (
        <div className="mb-3 px-3 py-2 text-xs text-rose-400 bg-rose-400/5 border border-rose-400/20 rounded-sm">
          {err}
        </div>
      )}

      <div className="flex flex-wrap gap-2 mb-4" data-testid="screener-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => handleTab(t.key)}
            className={"tab-btn " + (sort === t.key ? "tab-btn-active" : "")}
            data-testid={`tab-${t.key}`}
          >
            {t.icon ? <t.icon size={11} strokeWidth={1.75} /> : null}
            {t.label}
          </button>
        ))}
      </div>

      <div className="relative mb-3">
        <Search
          size={14}
          strokeWidth={1.5}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-400 pointer-events-none"
        />
        <input
          type="text"
          placeholder={sort === "trending" ? "Search CryptoRank trending symbol..." : "Search symbol (BTC, ETH, SOL...)"}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="input-base pl-9"
          data-testid="search-input"
        />
      </div>

      <div className="border border-ink-600 rounded-sm overflow-hidden">
        <div className="max-h-[560px] overflow-auto">
          <table className="w-full text-sm" data-testid="screener-table">
            <thead className="sticky top-0 bg-ink-800 z-10">
              <tr className="border-b border-ink-600">
                <Th className="text-left pl-4">Token</Th>
                <Th className="text-left">Stage</Th>
                <Th>Price</Th>
                <Th>
                  24h % <ArrowUpDown size={10} strokeWidth={1.5} className="inline -mt-0.5" />
                </Th>
                <Th>Volume</Th>
                <Th>Range</Th>
                <Th className="text-right pr-4">Candidate</Th>
              </tr>
            </thead>
            <tbody>
              {isLoading && activeRows.length === 0 && (
                <SkeletonRows count={12} />
              )}
              {!isLoading && filtered.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-10 text-center text-ink-400 text-sm">
                    No tokens match your filter.
                  </td>
                </tr>
              )}
              {filtered.map((r) => {
                const hot = sort === "shortCandidates" ? r.shortCandidateScore : r.trendingScore;
                const change = r.priceChangePercent || 0;
                const positive = change >= 0;
                return (
                  <tr
                    key={r.symbol}
                    onClick={() => openToken(r.symbol)}
                    className="border-b border-ink-600 last:border-b-0 hover:bg-ink-700 cursor-pointer transition-colors"
                    data-testid={`row-${r.symbol}`}
                  >
                    <td className="pl-4 py-3 text-left min-w-[150px]">
                      <div className="font-display text-sm text-ink-50 font-medium">{r.symbol}</div>
                      <div className="font-mono text-[10px] text-ink-300 mt-0.5 tabular-nums">
                        top-range {Math.round((r.positionInRange || 0) * 100)}% · rej {Math.round((r.upperRejection || 0) * 100)}%
                      </div>
                    </td>
                    <td className="py-3 px-3 text-left min-w-[210px]">
                      <span className={`inline-flex px-2 py-1 rounded-sm border text-[10px] font-mono uppercase ${r.tone}`}>
                        {sort === "shortCandidates" ? r.stage : "Radar"}
                      </span>
                      {sort === "shortCandidates" && (
                        <div className="text-[10px] text-ink-300 leading-snug mt-1 max-w-[260px]">
                          {r.reason}
                        </div>
                      )}
                    </td>
                    <Td>
                      <span className="font-mono tabular-nums text-ink-50">
                        {formatCurrency(r.lastPrice)}
                      </span>
                    </Td>
                    <Td>
                      <span
                        className={
                          "font-mono tabular-nums " +
                          (positive ? "text-emerald-400" : "text-rose-400")
                        }
                      >
                        {formatSignedPercent(change / 100, 2)}
                      </span>
                    </Td>
                    <Td>
                      <span className="font-mono tabular-nums text-ink-200">
                        {formatCompact(r.quoteVolume)}
                      </span>
                    </Td>
                    <Td>
                      <span className="font-mono tabular-nums text-ink-200">
                        {formatPercent(r.volatility24h || 0, 2)}
                      </span>
                    </Td>
                    <td className="pr-4 py-3 text-right">
                      <HotBar score={hot} risk={r.stopHuntRisk} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <p className="mt-3 text-[11px] text-ink-400 leading-relaxed">
        {sort === "trending"
          ? "Trending tab uses CryptoRank when configured, then falls back to internal trend ranking."
          : "Short Candidate Score sekarang bukan sekadar hot score. Ia memakai pump score, fuel weakening proxy, confirmation proxy, Monte Carlo edge proxy, dan penalti stop-hunt. Validasi final tetap di halaman Analyze."}
      </p>
    </div>
  );
}

function Th({ children, className = "" }) {
  return (
    <th
      className={
        "py-2.5 px-3 font-mono text-[10px] uppercase tracking-wider2 font-medium text-ink-300 text-right " +
        className
      }
    >
      {children}
    </th>
  );
}

function Td({ children }) {
  return <td className="py-3 px-3 text-right tabular-nums">{children}</td>;
}

function HotBar({ score = 0, risk = 0 }) {
  const pct = Math.round(clamp(score) * 100);
  const riskPct = Math.round(clamp(risk) * 100);
  const tone = riskPct >= 66 ? "bg-rose-400" : pct >= 70 ? "bg-emerald-400" : pct >= 40 ? "bg-amber-400" : "bg-ink-500";
  return (
    <div className="inline-flex items-center gap-2">
      <div className="w-14 h-1 bg-ink-600 rounded-sm overflow-hidden">
        <div className={"h-full " + tone} style={{ width: pct + "%" }} />
      </div>
      <span className="font-mono text-xs tabular-nums text-ink-100 w-7 text-right">
        {pct}
      </span>
    </div>
  );
}

function SkeletonRows({ count = 8 }) {
  return Array.from({ length: count }).map((_, i) => (
    <tr key={i} className="border-b border-ink-600">
      {Array.from({ length: 7 }).map((__, j) => (
        <td key={j} className="px-3 py-3.5">
          <div className="h-3 skeleton rounded-sm" />
        </td>
      ))}
    </tr>
  ));
}
