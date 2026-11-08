import React, { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ArrowLeft,
  CheckCircle2,
  CircleAlert,
  ShieldAlert,
  TrendingUp,
  XCircle,
} from "lucide-react";
import { fetchSymbol } from "../lib/api";
import { fmtDate, fmtPct, fmtPrice, fmtProb, fmtUsdCompact } from "../lib/format";
import VerdictBadge from "../components/VerdictBadge";
import ScoreBar from "../components/ScoreBar";

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-lineStrong bg-card px-3 py-2 shadow-lift text-[11px]">
      <p className="font-mono text-[10px] uppercase tracking-micro text-muted mb-1">{label}</p>
      {payload.map((p) => (
        <p key={p.dataKey} className="font-mono tabular-nums text-ink flex items-center justify-between gap-4">
          <span className="text-muted">{p.name}</span>
          <span>{p.dataKey === "close" ? `$${fmtPrice(p.value)}` : p.dataKey === "takerBuyRatio" ? p.value.toFixed(3) : fmtUsdCompact(p.value)}</span>
        </p>
      ))}
    </div>
  );
}

function MetricTile({ label, value, sub, tone = "ink" }) {
  const color = tone === "up" ? "text-up" : tone === "down" ? "text-down" : tone === "gold" ? "text-gold-600" : "text-ink";
  return (
    <div className="rounded-xl border border-line bg-paper px-4 py-3">
      <p className="micro-label !text-[9px]">{label}</p>
      <p className={`mt-1 font-mono text-[15px] font-semibold tabular-nums ${color}`}>{value}</p>
      {sub && <p className="text-[10px] text-muted mt-0.5 leading-4">{sub}</p>}
    </div>
  );
}

export default function TokenPage() {
  const { symbol } = useParams();
  const [state, setState] = useState({ loading: true, error: null, data: null });

  useEffect(() => {
    let alive = true;
    setState({ loading: true, error: null, data: null });
    fetchSymbol(symbol)
      .then((data) => alive && setState({ loading: false, error: null, data }))
      .catch((err) => alive && setState({ loading: false, error: err.message || "Failed to load asset.", data: null }));
    return () => {
      alive = false;
    };
  }, [symbol]);

  if (state.loading) {
    return (
      <div className="mx-auto max-w-7xl px-4 sm:px-6 py-10">
        <div className="skeleton h-5 w-40" />
        <div className="skeleton h-12 w-72 mt-6" />
        <div className="skeleton h-64 w-full mt-8" />
      </div>
    );
  }

  if (state.error || !state.data) {
    return (
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-16 text-center">
        <XCircle size={40} strokeWidth={1.5} className="text-down mx-auto" />
        <h1 className="font-display text-2xl font-semibold mt-4">Couldn’t load {symbol}</h1>
        <p className="text-muted text-sm mt-2">{state.error}</p>
        <Link to="/" className="btn-primary focus-ring mt-6 inline-flex">Back to screener</Link>
      </div>
    );
  }

  const d = state.data;
  const f = d.features || {};
  const s = d.scores || {};
  const m = d.market || {};
  const risk = d.risk || {};
  const filters = d.filters || {};
  const change = m.priceChangePercent ?? 0;
  const isDemo = d.sources ? !d.sources.live : true;
  const series = (d.series || []).map((p) => ({ ...p, label: fmtDate(p.t) }));
  const triggeredFilters = Object.entries(filters).filter(([, v]) => v === true).map(([k]) => k);

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-8 md:py-10">
      {/* ---------- breadcrumb + headline ---------- */}
      <Link to="/" className="inline-flex items-center gap-2 text-[12px] font-medium text-muted hover:text-pine-700 transition-colors focus-ring rounded">
        <ArrowLeft size={14} strokeWidth={2} /> Back to screener
      </Link>

      <div className="mt-5 flex flex-col lg:flex-row lg:items-end lg:justify-between gap-6 border-b border-line pb-6">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-display text-4xl sm:text-5xl font-semibold tracking-tight text-ink">
              {d.symbol.replace(/USDT$/, "")}
              <span className="text-faint text-3xl align-top">/USDT</span>
            </h1>
            <VerdictBadge tone={d.verdictTone} label={d.verdictLabel || d.verdict} size="lg" />
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-4 font-mono text-[12px] tabular-nums">
            <span className="text-xl text-ink font-semibold">${fmtPrice(d.price)}</span>
            <span className={change >= 0 ? "text-up" : "text-down"}>
              {change >= 0 ? "▲" : "▼"} {Math.abs(change).toFixed(2)}% 24h
            </span>
            {m.quoteVolume != null && <span className="text-muted">24h vol {fmtUsdCompact(m.quoteVolume)}</span>}
            <span className="text-faint">VWAP-20 {fmtPrice(f.vwap20)} ({fmtPct(f.closeVsVwap20, { signed: true })})</span>
          </div>
          <p className="micro-label !text-[9px] mt-3">
            {isDemo ? "Demo seed candles" : `Live candles · ${d.sources?.klines}`} · last 90 daily bars
          </p>
        </div>
        <div className="flex items-center gap-2">
          <TrendingUp size={16} className="text-gold-500" strokeWidth={1.75} />
          <p className="text-[11px] text-muted max-w-[220px] leading-4">Accumulation thesis: demand building ahead of price recognition.</p>
        </div>
      </div>

      {/* ---------- verdict banner ---------- */}
      <div className="mt-6 rounded-2xl bg-pine-800 text-paper px-6 py-6 md:px-8 md:py-7 shadow-card">
        <p className="micro-label !text-gold-300">Model verdict</p>
        <h2 className="font-display text-2xl sm:text-3xl font-semibold tracking-tight mt-2 text-paper">{d.verdictLabel || d.verdict}</h2>
        <ul className="mt-4 grid md:grid-cols-2 gap-x-8 gap-y-2.5">
          {(d.reasons || []).map((reason, i) => {
            const negative = /reject|flat\/negative|died|exodus|excluded|no edge|not met/i.test(reason);
            const Icon = negative ? XCircle : CheckCircle2;
            return (
              <li key={i} className="flex items-start gap-2.5 text-[12.5px] leading-5 text-pine-100">
                <Icon size={15} strokeWidth={2} className={`mt-0.5 shrink-0 ${negative ? "text-[#E8A79A]" : "text-gold-300"}`} />
                <span>{reason}</span>
              </li>
            );
          })}
        </ul>
      </div>

      {/* ---------- trigger chips ---------- */}
      {triggeredFilters.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="micro-label !text-[9px]">Triggered filters:</span>
          {triggeredFilters.map((key) => (
            <span key={key} className="chip border-[#E3B7AC] bg-[#F6E4E0] text-[#8A3324]">{key.replace(/([A-Z])/g, " $1").trim()}</span>
          ))}
        </div>
      )}

      {/* ---------- charts + evidence ---------- */}
      <div className="mt-6 grid lg:grid-cols-3 gap-4">
        <div className="card p-5 lg:col-span-2">
          <div className="flex items-center justify-between">
            <h3 className="font-display text-base font-semibold">Price vs demand accumulation</h3>
            <span className="micro-label !text-[9px]">90d · daily</span>
          </div>
          <div className="h-56 mt-4">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={series} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="#ECE9E2" strokeDasharray="3 4" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 10, fill: "#9AA096" }} tickLine={false} axisLine={false} minTickGap={40} />
                <YAxis domain={["dataMin", "dataMax"]} tick={{ fontSize: 10, fill: "#9AA096" }} tickLine={false} axisLine={false} width={52} tickFormatter={(v) => (v >= 1000 ? v.toLocaleString("en-US", { notation: "compact" }) : v)} />
                <Tooltip content={<ChartTooltip />} />
                <ReferenceLine y={f.vwap20} stroke="#B18A44" strokeDasharray="6 5" strokeWidth={1.25} />
                <Area type="monotone" dataKey="close" name="Close" stroke="#144338" strokeWidth={2} fill="#144338" fillOpacity={0.09} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <h4 className="font-display text-sm font-semibold mt-5 text-ink">Unit volume vs buy-side share</h4>
          <div className="h-44 mt-2">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={series} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="#ECE9E2" strokeDasharray="3 4" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 10, fill: "#9AA096" }} tickLine={false} axisLine={false} minTickGap={40} />
                <YAxis yAxisId="vol" orientation="left" tick={{ fontSize: 10, fill: "#9AA096" }} tickLine={false} axisLine={false} width={46} tickFormatter={(v) => v.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 })} />
                <YAxis yAxisId="ratio" orientation="right" domain={[0.3, 0.75]} tick={{ fontSize: 10, fill: "#9AA096" }} tickLine={false} axisLine={false} width={30} tickFormatter={(v) => v.toFixed(2)} />
                <Tooltip content={<ChartTooltip />} />
                <Bar yAxisId="vol" dataKey="baseVolume" name="Base volume" fill="#D2B985" radius={[2, 2, 0, 0]} maxBarSize={14} />
                <Line yAxisId="ratio" type="monotone" dataKey="takerBuyRatio" name="Taker buy" stroke="#1F4A38" strokeWidth={1.75} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* ---------- evidence panel ---------- */}
        <div className="card p-5 flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <h3 className="font-display text-base font-semibold">Evidence</h3>
            <span className="micro-label !text-[9px]">0–100</span>
          </div>
          <ScoreBar label="Demand quality" value={s.demandQuality ?? 0} tone="pine" />
          <ScoreBar label="Signal strength" value={s.signal ?? 0} tone="gold" />
          <ScoreBar label="Instability risk" value={s.instability ?? 0} tone="red" invertNote="Higher = more filing-style red flags. Excluded from longs at ≥ 55." />
          <div className="border-t border-line pt-4 grid grid-cols-2 gap-3">
            <div>
              <p className="micro-label !text-[9px]">p(acceleration)</p>
              <p className="font-mono text-2xl font-semibold text-pine-800 tabular-nums mt-1">{fmtProb(s.pAcceleration)}</p>
              <p className="text-[10px] text-muted leading-4 mt-0.5">heuristic, uncalibrated</p>
            </div>
            <div>
              <p className="micro-label !text-[9px]">p(adverse event)</p>
              <p className="font-mono text-2xl font-semibold text-down tabular-nums mt-1">{fmtProb(s.pAdverse)}</p>
              <p className="text-[10px] text-muted leading-4 mt-0.5">instability tail risk proxy</p>
            </div>
          </div>
          <div className="border-t border-line pt-4">
            <p className="micro-label !text-[9px] mb-2">Red-flag scan · 30d</p>
            {risk.abnormalEvents30d > 0 ? (
              <div className="flex items-start gap-2.5 text-[12px] leading-5 text-[#8A3324]">
                <ShieldAlert size={16} strokeWidth={2} className="mt-0.5 shrink-0" />
                <span>
                  <strong>{risk.abnormalEvents30d} abnormal-move day{risk.abnormalEvents30d > 1 ? "s" : ""}</strong> detected (clustered: {fmtPct(risk.clusteredEvents, { digits: 0 })}).
                  Correction magnitude {(risk.correctionMagnitude ?? 0).toFixed(2)} vs normal days.
                </span>
              </div>
            ) : (
              <div className="flex items-center gap-2.5 text-[12px] text-up">
                <CheckCircle2 size={16} strokeWidth={2} />
                <span>No abnormal-move days in the last 30 sessions.</span>
              </div>
            )}
            <p className="text-[9px] text-faint mt-2 leading-4">Internal-control inputs (material weakness disclosures) have no public crypto equivalent — explicitly unscored.</p>
          </div>
        </div>
      </div>

      {/* ---------- metric grid ---------- */}
      <div className="mt-6 grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricTile label="Organic demand Δ30d" value={fmtPct(f.gOrganic, { signed: true })} tone={f.gOrganic >= 0 ? "up" : "down"} sub={`vs reported USD ${fmtPct(f.gQuoteVolReported, { signed: true })}`} />
        <MetricTile label="Acceleration" value={fmtPct(f.acceleration, { signed: true })} tone={f.acceleration >= 0 ? "up" : "down"} sub="demand growth vs prior window" />
        <MetricTile label="Lead vs price" value={fmtPct(f.leadVsPrice, { signed: true })} tone={f.leadVsPrice >= 0 ? "up" : "down"} sub="organic demand − price growth" />
        <MetricTile label="Demand–price gap" value={fmtPct(f.gap, { signed: true })} tone="gold" sub="under-recognition multiple" />
        <MetricTile label="Cash conversion" value={(f.cashConversion ?? 0).toFixed(2)} sub="taker buy share of unit volume" />
        <MetricTile label="Flow stability" value={(f.flowStability ?? 0).toFixed(2)} sub="participation trend decay" />
        <MetricTile label="Retention quality" value={(f.retentionQuality ?? 0).toFixed(2)} sub="NRR & GRR analogs, churn-penalised" />
        <MetricTile label="Distortion removed" value={fmtPct(f.distortionRemoved, { signed: true })} sub="price & mix stripped from reported growth" />
      </div>

      {/* ---------- exit triggers ---------- */}
      <div className="mt-6 card px-5 py-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div className="flex items-start gap-3">
          <CircleAlert size={18} strokeWidth={1.75} className="text-warn mt-0.5 shrink-0" />
          <div>
            <p className="micro-label !text-[9px]">Exit triggers for existing positions</p>
            {(d.exitTriggers || []).length > 0 ? (
              <ul className="mt-1.5 text-[12px] text-ink leading-5 list-disc list-inside">
                {d.exitTriggers.map((t) => <li key={t}>{t}</li>)}
              </ul>
            ) : (
              <p className="mt-1.5 text-[12px] text-muted">No exit trigger active: organic demand positive, engagement stable.</p>
            )}
          </div>
        </div>
        <Link to="/methodology" className="btn-ghost focus-ring shrink-0">Read the model</Link>
      </div>
    </div>
  );
}
