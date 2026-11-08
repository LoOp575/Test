import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  FlaskConical,
  RefreshCw,
  Search,
  ShieldAlert,
} from "lucide-react";
import { fetchScreen } from "../lib/api";
import { useLiveTickers } from "../lib/useLiveTickers";
import { fmtPct, fmtPrice, fmtProb, fmtUsdCompact } from "../lib/format";
import VerdictBadge from "../components/VerdictBadge";
import ScoreBar from "../components/ScoreBar";
import StatTile from "../components/StatTile";

const VERDICT_FILTERS = [
  { id: "ALL", label: "All" },
  { id: "LONG", label: "Long candidates" },
  { id: "WATCH", label: "Watchlist" },
  { id: "NEUTRAL", label: "Neutral" },
  { id: "NO_SIGNAL", label: "No signal" },
  { id: "AVOID", label: "Avoid" },
];

const SORTERS = {
  signal: (r) => r.scores?.signal ?? 0,
  quality: (r) => r.scores?.demandQuality ?? 0,
  organic: (r) => r.features?.gOrganic ?? -Infinity,
  change: (r) => r.market?.priceChangePercent ?? 0,
  volume: (r) => r.market?.quoteVolume ?? 0,
  pacc: (r) => r.scores?.pAcceleration ?? 0,
};

function SkeletonRows({ rows = 8 }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, i) => (
        <tr key={i}>
          {Array.from({ length: 10 }).map((__, j) => (
            <td key={j} className="px-4 py-3">
              <div className="skeleton h-4 w-full" style={{ opacity: 1 - i * 0.09 }} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const [state, setState] = useState({ loading: true, error: null, payload: null });
  const [query, setQuery] = useState("");
  const [verdict, setVerdict] = useState("ALL");
  const [sortKey, setSortKey] = useState("signal");
  const [sortDir, setSortDir] = useState("desc");
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    try {
      setRefreshing(true);
      const payload = await fetchScreen(18);
      setState({ loading: false, error: null, payload });
    } catch (err) {
      setState((s) => ({ loading: false, error: err.message || "Failed to load screen.", payload: s.payload }));
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const rows = state.payload?.data ?? [];
  const rowSymbols = useMemo(() => rows.map((r) => r.symbol), [rows]);
  const { ticks: liveTicks, live } = useLiveTickers(rowSymbols);

  const filtered = useMemo(() => {
    let out = rows.filter((r) => r.ok !== false);
    if (verdict !== "ALL") out = out.filter((r) => r.verdict === verdict);
    if (query.trim()) {
      const q = query.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
      out = out.filter((r) => r.symbol.includes(q));
    }
    const dir = sortDir === "desc" ? -1 : 1;
    const getter = SORTERS[sortKey] || SORTERS.signal;
    return [...out].sort((a, b) => (getter(a) - getter(b)) * dir);
  }, [rows, verdict, query, sortKey, sortDir]);

  const summary = state.payload?.summary;
  const isDemo = state.payload ? !state.payload.live : false;

  const toggleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const SortIcon = ({ column }) =>
    sortKey !== column ? (
      <ArrowUpDown size={11} strokeWidth={1.75} className="opacity-40" />
    ) : sortDir === "desc" ? (
      <ArrowDown size={11} strokeWidth={2.25} className="text-pine-700" />
    ) : (
      <ArrowUp size={11} strokeWidth={2.25} className="text-pine-700" />
    );

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6">
      {/* ================= hero ================= */}
      <section className="pt-10 md:pt-14 pb-8 md:pb-10 border-b border-line">
        <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-6">
          <div className="max-w-2xl">
            <p className="micro-label text-gold-600 !text-[10px]">Deferred-demand quality model · Daily OHLCV</p>
            <h1 className="font-display font-semibold tracking-tight text-ink text-4xl sm:text-5xl xl:text-[56px] leading-[1.05] mt-3">
              Demand tells you
              <br />
              <span className="italic text-pine-700">before price does.</span>
            </h1>
            <p className="mt-4 text-[15px] leading-6 text-muted max-w-xl">
              Meridian adapts the deferred-revenue quality model to crypto: it separates{" "}
              <span className="text-ink font-medium">organic unit demand</span> from price-driven
              volume, scores retention and buy-side conversion, and flags filing-style instability —
              so you can screen for accumulation, not headlines.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={load} disabled={refreshing} className="btn-primary focus-ring disabled:opacity-60">
              <RefreshCw size={15} strokeWidth={2} className={refreshing ? "animate-spin" : ""} />
              {refreshing ? "Refreshing…" : "Refresh screen"}
            </button>
          </div>
        </div>

        {/* demo banner */}
        {isDemo && (
          <div className="mt-6 flex items-start gap-3 rounded-xl border border-gold-300 bg-gold-100 px-4 py-3">
            <FlaskConical size={17} strokeWidth={1.75} className="text-gold-700 mt-0.5 shrink-0" />
            <p className="text-[12px] leading-5 text-gold-700">
              <span className="font-semibold">Demo data.</span> Live exchange APIs are unreachable
              from this server right now, so you are seeing deterministic sample candles. Deploy with
              outbound access to Binance / MEXC / CoinGecko for live scoring — or set{" "}
              <code className="font-mono text-[11px] bg-paper px-1.5 py-0.5 rounded border border-gold-200">SCREEN_SOURCE=live</code>.
            </p>
          </div>
        )}
      </section>

      {/* ================= summary tiles ================= */}
      <section className="py-6 grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatTile label="Universe" value={summary ? summary.universe : "—"} sub="Top USDT pairs by 24h quote volume" />
        <StatTile label="Long candidates" value={summary ? summary.longCandidates : "—"} sub="Demand-quality criteria met" tone="pine" />
        <StatTile label="Watchlist" value={summary ? summary.watchlist : "—"} sub="Positive demand, awaiting confirmation" tone="gold" />
        <StatTile label="Avoid / red flags" value={summary ? summary.avoid : "—"} sub="Low-quality growth or instability" tone="red" />
      </section>

      {/* ================= controls ================= */}
      <section className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 pb-4">
        <div className="flex flex-wrap items-center gap-1.5">
          {VERDICT_FILTERS.map((f) => (
            <button
              key={f.id}
              onClick={() => setVerdict(f.id)}
              className={`focus-ring rounded-full px-3 py-1.5 text-[12px] font-medium border transition-colors ${
                verdict === f.id
                  ? "bg-pine-800 text-paper border-pine-800"
                  : "bg-card text-muted border-lineStrong hover:text-ink hover:border-pine-400"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <label className="relative block md:w-64">
          <Search size={15} strokeWidth={1.75} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-faint pointer-events-none" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search asset — e.g. SOL"
            className="focus-ring w-full rounded-full border border-lineStrong bg-card pl-10 pr-4 py-2.5 text-[13px] placeholder:text-faint"
            aria-label="Search asset"
          />
        </label>
      </section>

      {/* ================= table ================= */}
      <section className="card overflow-hidden mb-10 animate-fade-up">
        <div className="flex items-center justify-between px-5 pt-5 pb-3 border-b border-line">
          <div>
            <h2 className="font-display text-lg font-semibold tracking-tight">Ranked screen</h2>
            <p className="text-[11px] text-muted mt-0.5">Click any row for the full breakdown · sorted by signal by default</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="chip border-lineStrong text-muted">Avg signal {summary ? summary.avgSignal.toFixed(0) : "—"}</span>
            {live && (
              <span className="chip-live" title="Prices streaming live from Binance public WebSocket">
                <span className="live-dot" /> Live
              </span>
            )}
          </div>
        </div>

        {state.error && (
          <div className="mx-5 mt-4 flex items-start gap-3 rounded-xl border border-[#E3B7AC] bg-[#F6E4E0] px-4 py-3">
            <ShieldAlert size={17} className="text-[#8A3324] mt-0.5 shrink-0" />
            <div className="text-[12px] text-[#8A3324]">
              <p className="font-semibold">Screen failed to load</p>
              <p className="mt-0.5">{state.error}</p>
              <button onClick={load} className="mt-2 underline underline-offset-2 font-medium">Try again</button>
            </div>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="data-table min-w-[1080px]">
            <thead>
              <tr>
                <th className="w-10">#</th>
                <th>Asset</th>
                <th className="text-right">Price</th>
                <th className="text-right sortable" onClick={() => toggleSort("change")}>
                  <span className="inline-flex items-center gap-1">24h <SortIcon column="change" /></span>
                </th>
                <th className="text-right sortable" onClick={() => toggleSort("volume")}>
                  <span className="inline-flex items-center gap-1">24h vol <SortIcon column="volume" /></span>
                </th>
                <th className="text-right sortable" onClick={() => toggleSort("organic")}>
                  <span className="inline-flex items-center gap-1">Organic Δ30d <SortIcon column="organic" /></span>
                </th>
                <th className="sortable w-40" onClick={() => toggleSort("quality")}>
                  <span className="inline-flex items-center gap-1">Quality <SortIcon column="quality" /></span>
                </th>
                <th className="sortable w-40" onClick={() => toggleSort("signal")}>
                  <span className="inline-flex items-center gap-1">Signal <SortIcon column="signal" /></span>
                </th>
                <th className="text-right sortable" onClick={() => toggleSort("pacc")}>
                  <span className="inline-flex items-center gap-1">p(accel) <SortIcon column="pacc" /></span>
                </th>
                <th>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {state.loading ? (
                <SkeletonRows />
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={10} className="py-14 text-center">
                    <p className="font-display text-lg text-ink">Nothing matches that filter.</p>
                    <p className="text-[12px] text-muted mt-1">Clear the search or pick a different verdict.</p>
                  </td>
                </tr>
              ) : (
                filtered.map((r, i) => {
                  const f = r.features || {};
                  const s = r.scores || {};
                  const m = r.market || {};
                  const lt = liveTicks[r.symbol];
                  const chg = lt?.changePercent ?? m.priceChangePercent ?? 0;
                  const price = lt?.price ?? r.price;
                  const quoteVol = lt?.quoteVolume ?? m.quoteVolume;
                  const org = f.gOrganic ?? 0;
                  return (
                    <tr key={r.symbol} className="row-link" onClick={() => navigate(`/token/${r.symbol}`)}>
                      <td className="font-mono text-[11px] text-faint">{String(i + 1).padStart(2, "0")}</td>
                      <td>
                        <div className="flex items-center gap-2.5">
                          <span className="grid place-items-center w-8 h-8 rounded-lg bg-pine-50 border border-pine-100 font-display text-[13px] font-semibold text-pine-800">
                            {r.symbol.replace(/USDT$/, "").slice(0, 2)}
                          </span>
                          <span>
                            <span className="block font-semibold text-[13px] tracking-tight text-ink">
                              {r.symbol.replace(/USDT$/, "")}
                              <span className="text-faint font-normal">/USDT</span>
                            </span>
                            <span className="block text-[10px] uppercase tracking-micro text-faint">{isDemo ? "demo" : "spot"}</span>
                          </span>
                        </div>
                      </td>
                      <td className="text-right font-mono text-[12px] tabular-nums text-ink">${fmtPrice(price)}</td>
                      <td className={`text-right font-mono text-[12px] tabular-nums ${chg >= 0 ? "text-up" : "text-down"}`}>
                        {chg >= 0 ? "+" : ""}{chg.toFixed(2)}%
                      </td>
                      <td className="text-right font-mono text-[12px] tabular-nums text-muted">{fmtUsdCompact(quoteVol)}</td>
                      <td className={`text-right font-mono text-[12px] tabular-nums font-semibold ${org >= 0 ? "text-up" : "text-down"}`}>
                        {fmtPct(org, { signed: true, digits: 1 })}
                      </td>
                      <td><ScoreBar label="" value={s.demandQuality ?? 0} tone="pine" /></td>
                      <td><ScoreBar label="" value={s.signal ?? 0} tone="gold" /></td>
                      <td className="text-right font-mono text-[12px] tabular-nums text-ink">{fmtProb(s.pAcceleration)}</td>
                      <td>
                        <VerdictBadge tone={r.verdictTone} label={r.verdictLabel || r.verdict} />
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        <div className="px-5 py-3 border-t border-line flex flex-wrap items-center justify-between gap-2">
          <p className="text-[10px] text-faint max-w-lg leading-4">
            Organic Δ = growth in unit (base-asset) volume, price/mix effect removed. Lead = organic demand growth minus price growth. Signal &gt; 55 with p(accel) &gt; 60% and price &gt; VWAP-20 = long candidate. Prices tick live from Binance public WebSocket when connected.
          </p>
          <span className="micro-label !text-[9px]">{filtered.length} shown</span>
        </div>
      </section>
    </div>
  );
}
