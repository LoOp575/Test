import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  Clock,
  Lock,
  Minus,
  Radio,
  RefreshCw,
  ShieldCheck,
  Trash2,
  XCircle,
} from "lucide-react";
import { fetchFpV2Intraday, postFpV2IntradayWs } from "../lib/api";
import { connectIntradayWsAccumulator } from "../lib/liveOrderbookV2";

const QUICK_SYMBOLS = [
  "BTCUSDT",
  "ETHUSDT",
  "SOLUSDT",
  "BNBUSDT",
  "XRPUSDT",
  "DOGEUSDT",
  "ADAUSDT",
  "AVAXUSDT",
  "LINKUSDT",
  "SUIUSDT",
];

const HORIZON_OPTIONS = [
  { value: 15, label: "15 Menit" },
  { value: 30, label: "30 Menit" },
  { value: 60, label: "60 Menit" },
];

const BAND_OPTIONS = [
  { value: "auto", label: "Otomatis (±σ)" },
  { value: "0.0035", label: "±0.35%" },
  { value: "0.005", label: "±0.50%" },
  { value: "0.008", label: "±0.80%" },
  { value: "0.012", label: "±1.20%" },
];

const LOCK_STORAGE_KEY = "meridian_fp_v2_intraday_locks_v1";

function loadLocks() {
  try {
    const raw = window.localStorage.getItem(LOCK_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.slice(0, 30) : [];
  } catch {
    return [];
  }
}

function saveLocks(list) {
  try {
    window.localStorage.setItem(LOCK_STORAGE_KEY, JSON.stringify(list.slice(0, 30)));
  } catch {
    /* ignore storage quota */
  }
}

function fmtPrice(v) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
  const n = Number(v);
  if (n >= 1000) {
    return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
  if (n >= 1) return `$${n.toFixed(4)}`;
  if (n >= 0.01) return `$${n.toFixed(5)}`;
  return `$${n.toFixed(7)}`;
}

function fmtProb(v, digits = 1) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
  return `${(Number(v) * 100).toFixed(digits)}%`;
}

function fmtPctPoints(pctVal, digits = 2) {
  if (pctVal === null || pctVal === undefined || !Number.isFinite(Number(pctVal))) return "—";
  const n = Number(pctVal);
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

function fmtUtcTime(ms) {
  if (!ms) return "—";
  try {
    const d = new Date(ms);
    return `${d.toISOString().slice(11, 16)} UTC`;
  } catch {
    return "—";
  }
}

function fmtCountdown(msLeft) {
  const s = Math.max(0, Math.floor(msLeft / 1000));
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${String(m).padStart(2, "0")}:${String(rem).padStart(2, "0")}`;
}

function callStyles(code) {
  if (code === "NAIK_KUAT") {
    return {
      card: "border-emerald-300 bg-gradient-to-br from-emerald-50/90 via-card to-card",
      badge: "bg-pine-700 text-white border-pine-800",
      confidence: "Kuat",
    };
  }
  if (code === "NAIK_TIPIS") {
    return {
      card: "border-emerald-200 bg-gradient-to-br from-emerald-50/50 via-card to-card",
      badge: "bg-emerald-100 text-emerald-900 border-emerald-300",
      confidence: "Tipis (Disiplin Stop)",
    };
  }
  if (code === "TURUN_KUAT") {
    return {
      card: "border-rose-300 bg-gradient-to-br from-rose-50/90 via-card to-card",
      badge: "bg-rose-700 text-white border-rose-800",
      confidence: "Kuat",
    };
  }
  if (code === "TURUN_TIPIS") {
    return {
      card: "border-rose-200 bg-gradient-to-br from-rose-50/50 via-card to-card",
      badge: "bg-rose-100 text-rose-900 border-rose-300",
      confidence: "Tipis (Waspada)",
    };
  }
  return {
    card: "border-amber-200 bg-gradient-to-br from-amber-50/50 via-card to-card",
    badge: "bg-amber-100 text-amber-900 border-amber-300",
    confidence: "Sabar / Tunggu",
  };
}

function DirectionIcon({ shortDir, className = "w-4 h-4" }) {
  if (shortDir === "NAIK") return <ArrowUpRight className={className} />;
  if (shortDir === "TURUN") return <ArrowDownRight className={className} />;
  return <Minus className={className} />;
}

/* ---------- Forward Directional PDF Chart (overlayCurve: xPct, v1, v2) ---------- */

function IntradayPdfChart({
  overlayCurve = [],
  targetBand = 0.005,
  eqShiftPct = 0,
  horizonMin = 30,
}) {
  if (!Array.isArray(overlayCurve) || overlayCurve.length < 3) return null;
  const W = 620;
  const H = 230;
  const pad = { top: 22, right: 18, bottom: 34, left: 18 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  const bandPct = (Number(targetBand) || 0.005) * 100;
  const xs = overlayCurve.map((p) => Number(p.xPct) || 0);
  const minX = Math.min(...xs, -bandPct * 1.6);
  const maxX = Math.max(...xs, bandPct * 1.6);
  const maxY = Math.max(
    1e-6,
    ...overlayCurve.map((p) => Math.max(Number(p.v2) || 0, Number(p.v1) || 0))
  );

  const sx = (x) => pad.left + ((x - minX) / Math.max(1e-9, maxX - minX)) * plotW;
  const sy = (y) => pad.top + plotH - (y / maxY) * plotH;
  const baseY = pad.top + plotH;

  const lineV2 = overlayCurve
    .map((p, i) => `${i === 0 ? "M" : "L"}${sx(p.xPct).toFixed(1)},${sy(p.v2).toFixed(1)}`)
    .join(" ");
  const lineV1 = overlayCurve
    .map((p, i) => `${i === 0 ? "M" : "L"}${sx(p.xPct).toFixed(1)},${sy(p.v1).toFixed(1)}`)
    .join(" ");

  const buildHalfArea = (side) => {
    const pts = overlayCurve.filter((p) => (side === "down" ? p.xPct <= 0 : p.xPct >= 0));
    if (pts.length < 2) return "";
    const first = pts[0];
    const last = pts[pts.length - 1];
    const body = pts.map((p) => `L${sx(p.xPct).toFixed(1)},${sy(p.v2).toFixed(1)}`).join(" ");
    return `M${sx(first.xPct).toFixed(1)},${baseY.toFixed(1)} ${body} L${sx(last.xPct).toFixed(
      1
    )},${baseY.toFixed(1)} Z`;
  };

  const zeroX = sx(0);
  const stopX = sx(-bandPct);
  const tgtX = sx(bandPct);
  const eqClamped = Math.max(minX, Math.min(maxX, Number(eqShiftPct) || 0));
  const eqX = sx(eqClamped);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none">
      <path d={buildHalfArea("down")} fill="rgba(179, 64, 47, 0.14)" />
      <path d={buildHalfArea("up")} fill="rgba(20, 67, 56, 0.17)" />

      {/* Stop & Target lines */}
      <line
        x1={stopX}
        x2={stopX}
        y1={pad.top}
        y2={baseY}
        stroke="#B3402F"
        strokeWidth="1"
        strokeDasharray="3 3"
        opacity="0.75"
      />
      <line
        x1={tgtX}
        x2={tgtX}
        y1={pad.top}
        y2={baseY}
        stroke="#1F6F4A"
        strokeWidth="1"
        strokeDasharray="3 3"
        opacity="0.75"
      />
      {/* 0% center line */}
      <line
        x1={zeroX}
        x2={zeroX}
        y1={pad.top - 4}
        y2={baseY}
        stroke="#1F2721"
        strokeWidth="1.4"
      />

      {/* Equilibrium VWAP+MicroPrice anchor */}
      <circle cx={eqX} cy={baseY} r="3.8" fill="#B18A44" />

      {/* v1 dashed gold + v2 solid pine */}
      <path
        d={lineV1}
        fill="none"
        stroke="#B18A44"
        strokeWidth="1.7"
        strokeDasharray="5 3"
        opacity="0.9"
      />
      <path d={lineV2} fill="none" stroke="#144338" strokeWidth="2.4" />

      <line x1={pad.left} x2={W - pad.right} y1={baseY} y2={baseY} stroke="#D9D5CB" />

      <text x={pad.left + 6} y={pad.top + 11} fontSize="10" fill="#B3402F" fontWeight="600">
        Area Turun P(x &lt; 0)
      </text>
      <text
        x={W - pad.right - 6}
        y={pad.top + 11}
        textAnchor="end"
        fontSize="10"
        fill="#144338"
        fontWeight="600"
      >
        Area Naik P(x &gt; 0)
      </text>

      <text x={stopX} y={H - 10} textAnchor="middle" fontSize="10" fill="#B3402F">
        -{bandPct.toFixed(2)}%
      </text>
      <text x={zeroX} y={H - 10} textAnchor="middle" fontSize="10" fill="#1F2721" fontWeight="600">
        0% ({horizonMin}m)
      </text>
      <text x={tgtX} y={H - 10} textAnchor="middle" fontSize="10" fill="#144338">
        +{bandPct.toFixed(2)}%
      </text>
    </svg>
  );
}

/* ---------- 2-Hour 5m Taker CVD Mini Bar + Cumulative Line ---------- */

function CvdMiniChart({ series = [] }) {
  if (!Array.isArray(series) || !series.length) return null;
  const W = 520;
  const H = 122;
  const pad = { top: 10, right: 10, bottom: 20, left: 10 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;
  const midY = pad.top + plotH / 2;

  const maxDelta = Math.max(1e-6, ...series.map((s) => Math.abs(Number(s.delta) || 0)));
  const cums = series.map((s) => Number(s.cvd) || 0);
  const minCum = Math.min(...cums);
  const maxCum = Math.max(...cums);
  const cumSpan = Math.max(1e-6, maxCum - minCum);

  const step = plotW / series.length;
  const barW = Math.max(3, step * 0.62);

  const cumLine = series
    .map((s, i) => {
      const cx = pad.left + step * (i + 0.5);
      const cy = pad.top + plotH - (((Number(s.cvd) || 0) - minCum) / cumSpan) * plotH;
      return `${i === 0 ? "M" : "L"}${cx.toFixed(1)},${cy.toFixed(1)}`;
    })
    .join(" ");

  const first = series[0];
  const last = series[series.length - 1];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none">
      <line
        x1={pad.left}
        x2={W - pad.right}
        y1={midY}
        y2={midY}
        stroke="#D9D5CB"
        strokeDasharray="2 2"
      />
      {series.map((s, i) => {
        const d = Number(s.delta) || 0;
        const h = (Math.abs(d) / maxDelta) * (plotH * 0.45);
        const x = pad.left + step * i + (step - barW) / 2;
        const y = d >= 0 ? midY - h : midY;
        return (
          <rect
            key={s.t || i}
            x={x}
            y={y}
            width={barW}
            height={Math.max(1, h)}
            rx="1.2"
            fill={d >= 0 ? "rgba(31, 111, 74, 0.55)" : "rgba(179, 64, 47, 0.52)"}
          />
        );
      })}
      <path d={cumLine} fill="none" stroke="#1F2721" strokeWidth="1.7" />
      <text x={pad.left} y={H - 4} fontSize="9.5" fill="#68716A">
        {fmtUtcTime(first?.t)}
      </text>
      <text x={W / 2} y={H - 4} textAnchor="middle" fontSize="9.5" fill="#68716A">
        Hijau = Taker Buy · Merah = Taker Sell · Garis = CVD kumulatif 2 jam
      </text>
      <text x={W - pad.right} y={H - 4} textAnchor="end" fontSize="9.5" fill="#68716A">
        {fmtUtcTime(last?.t)}
      </text>
    </svg>
  );
}

/* ---------- Signed Factor Meter ---------- */

function FactorRow({ label, value, scale = 0.4, displayText }) {
  const norm = Math.max(-1, Math.min(1, (Number(value) || 0) / Math.max(1e-6, scale)));
  const pct = Math.abs(norm) * 50;
  const isPos = norm >= 0;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="font-medium text-ink">{label}</span>
        <span
          className={`font-mono tabular-nums font-semibold ${
            Math.abs(norm) < 0.08 ? "text-muted" : isPos ? "text-emerald-700" : "text-rose-700"
          }`}
        >
          {displayText}
        </span>
      </div>
      <div className="relative h-2 rounded-full bg-paper border border-line overflow-hidden">
        <div className="absolute inset-y-0 left-1/2 w-px bg-lineStrong" />
        <div
          className={`absolute inset-y-0 rounded-full ${
            isPos ? "bg-emerald-600" : "bg-rose-600"
          }`}
          style={
            isPos
              ? { left: "50%", width: `${pct}%` }
              : { left: `${50 - pct}%`, width: `${pct}%` }
          }
        />
      </div>
    </div>
  );
}

/* ---------- Main Intraday 15–30–60 Minute Forecast Tab ---------- */

export default function FpIntradayTab({ sourceMode = "auto", initialSymbol = "BTCUSDT" }) {
  const [symbol, setSymbol] = useState(() => {
    const raw = String(initialSymbol || "BTCUSDT").toUpperCase().trim();
    return raw.endsWith("USDT") ? raw : `${raw}USDT`;
  });
  const [customInput, setCustomInput] = useState("");
  const [horizonMin, setHorizonMin] = useState(30);
  const [bandChoice, setBandChoice] = useState("auto");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const [wsEnabled, setWsEnabled] = useState(false);
  const [wsStatus, setWsStatus] = useState("idle");
  const [wsMessage, setWsMessage] = useState("");
  const [wsMeta, setWsMeta] = useState(null);
  const latestWsRef = useRef(null);

  const [bundle, setBundle] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  // Live signal lock scoreboard (persisted in localStorage, auto-graded at dueAt)
  const [locks, setLocks] = useState(() => loadLocks());
  const [nowTs, setNowTs] = useState(() => Date.now());
  const gradingIdsRef = useRef(new Set());

  const targetBandNum = bandChoice === "auto" ? null : Number(bandChoice);

  const loadForecast = useCallback(
    async ({ silent = false, wsPayload = null } = {}) => {
      if (!silent) setLoading(true);
      else setRefreshing(true);
      setError("");
      try {
        let data;
        if (wsPayload) {
          data = await postFpV2IntradayWs(symbol, {
            horizonMin,
            ...(targetBandNum ? { targetBand: targetBandNum } : {}),
            source: sourceMode,
            tfi: wsPayload.tfi,
            obi: wsPayload.obi,
            spoofIndex: wsPayload.spoofIndex,
            absorptionIndex: wsPayload.absorptionIndex,
            midPrice: wsPayload.midPrice,
            microPrice: wsPayload.microPrice,
            accumulatedMin: wsPayload.accumulatedMin,
          });
        } else {
          data = await fetchFpV2Intraday(symbol, {
            horizonMin,
            targetBand: targetBandNum,
            source: sourceMode,
          });
        }
        setBundle(data);
      } catch (err) {
        setError(err?.message || "Gagal memuat proyeksi intraday 15–30 menit.");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [symbol, horizonMin, targetBandNum, sourceMode]
  );

  useEffect(() => {
    latestWsRef.current = null;
    setWsMeta(null);
    loadForecast({ silent: false });
  }, [loadForecast]);

  // Calm 60s refresh (paused when WS accumulator already refreshes calmly every 15s)
  useEffect(() => {
    if (!autoRefresh) return undefined;
    if (wsEnabled && wsStatus === "live") return undefined;
    const id = setInterval(() => {
      loadForecast({ silent: true, wsPayload: latestWsRef.current });
    }, 60_000);
    return () => clearInterval(id);
  }, [autoRefresh, wsEnabled, wsStatus, loadForecast]);

  // Optional 10-minute rolling Live WebSocket accumulator (calm 15s updates, zero jitter)
  useEffect(() => {
    if (!wsEnabled) {
      setWsStatus("idle");
      setWsMessage("");
      setWsMeta(null);
      latestWsRef.current = null;
      return undefined;
    }
    const stop = connectIntradayWsAccumulator(symbol, {
      emitIntervalMs: 15_000,
      windowMin: 10,
      onStatus: (st, msg) => {
        setWsStatus(st);
        setWsMessage(msg || "");
      },
      onSummary: (summary) => {
        latestWsRef.current = summary;
        setWsMeta(summary);
        loadForecast({ silent: true, wsPayload: summary });
      },
    });
    return () => stop?.();
  }, [wsEnabled, symbol, loadForecast]);

  // Countdown tick for pending live locked signals
  useEffect(() => {
    if (!locks.some((l) => l.status === "pending")) return undefined;
    const t = setInterval(() => setNowTs(Date.now()), 1000);
    return () => clearInterval(t);
  }, [locks]);

  // Automatic due-time grading against live price
  useEffect(() => {
    const due = locks.filter(
      (l) => l.status === "pending" && nowTs >= l.dueAt && !gradingIdsRef.current.has(l.id)
    );
    if (!due.length) return;

    due.forEach(async (item) => {
      gradingIdsRef.current.add(item.id);
      try {
        const res = await fetchFpV2Intraday(item.symbol, {
          horizonMin: item.horizonMin,
          source: sourceMode,
        });
        const exitPrice = Number(res?.forecast?.price);
        if (!Number.isFinite(exitPrice) || exitPrice <= 0) return;
        const pctMove = (exitPrice / item.entryPrice - 1) * 100;
        let outcome = "NEUTRAL";
        if (item.shortDir === "NAIK") outcome = pctMove > 0 ? "HIT" : "MISS";
        else if (item.shortDir === "TURUN") outcome = pctMove < 0 ? "HIT" : "MISS";

        setLocks((prev) => {
          const next = prev.map((x) =>
            x.id === item.id
              ? {
                  ...x,
                  status: "done",
                  exitPrice,
                  realizedPct: pctMove,
                  outcome,
                  gradedAt: Date.now(),
                }
              : x
          );
          saveLocks(next);
          return next;
        });
      } catch {
        /* retry next tick */
      } finally {
        gradingIdsRef.current.delete(item.id);
      }
    });
  }, [locks, nowTs, sourceMode]);

  const handleCustomSubmit = (e) => {
    e.preventDefault();
    const raw = customInput.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!raw) return;
    setSymbol(raw.endsWith("USDT") ? raw : `${raw}USDT`);
    setCustomInput("");
  };

  const handleLockCurrent = () => {
    const fc = bundle?.forecast;
    if (!fc) return;
    const now = Date.now();
    const hMin = Math.round(Number(fc.horizonMin) || horizonMin);
    const entry = {
      id: `${bundle.symbol}-${hMin}-${now}`,
      symbol: bundle.symbol,
      baseAsset: bundle.baseAsset,
      horizonMin: hMin,
      lockedAt: now,
      dueAt: now + hMin * 60_000,
      entryPrice: fc.price,
      shortDir: fc.call.short,
      label: fc.call.label,
      pUp: fc.stats.pUp,
      bothAgree: Boolean(fc.consensus?.bothActiveAgree),
      status: "pending",
    };
    setLocks((prev) => {
      const next = [entry, ...prev].slice(0, 30);
      saveLocks(next);
      return next;
    });
    setNowTs(now);
  };

  const handleClearLocks = () => {
    setLocks([]);
    saveLocks([]);
  };

  const liveScore = useMemo(() => {
    const gradedActive = locks.filter(
      (l) => l.status === "done" && (l.outcome === "HIT" || l.outcome === "MISS")
    );
    const hits = gradedActive.filter((l) => l.outcome === "HIT").length;
    const pending = locks.filter((l) => l.status === "pending").length;
    return {
      total: gradedActive.length,
      hits,
      winRate: gradedActive.length > 0 ? hits / gradedActive.length : null,
      pending,
    };
  }, [locks]);

  const fc = bundle?.forecast;
  const bt = bundle?.backtest;
  const matrix = useMemo(() => bundle?.horizonMatrix || [], [bundle]);
  const h15Row = useMemo(() => matrix.find((m) => m.horizonMin === 15), [matrix]);
  const alignmentDir = bundle?.aligned15m30m ? h15Row?.call?.short : null;

  const st = callStyles(fc?.call?.code);
  const q = fc?.stats?.priceQuantiles;
  const sig = fc?.signals;
  const isAutoBand = bandChoice === "auto";

  return (
    <div className="space-y-5">
      {/* Control Panel */}
      <div className="card p-4 sm:p-5 space-y-3.5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-ink">
              Proyeksi Arah Intraday 15 · 30 · 60 Menit (Fokker–Planck Multi-Timescale)
            </h2>
            <p className="text-xs text-muted mt-0.5 max-w-2xl">
              Menggabungkan arus beli/jual Taker nyata (CVD candle 5 menit), deteksi penyerapan
              tembok pasif, harga wajar VWAP <code className="font-mono">e^(−λd)</code> +
              Micro-Price, dan order book live yang meluruh otomatis terhadap waktu.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setWsEnabled((v) => !v)}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition ${
                wsEnabled
                  ? wsStatus === "live"
                    ? "bg-pine-800 text-paper border-pine-800"
                    : "bg-amber-50 text-amber-900 border-amber-300"
                  : "bg-paper text-ink border-lineStrong hover:border-pine-400"
              }`}
              title="Akumulasi aliran trade & order book live selama 10 menit, update tenang tiap 15 detik"
            >
              <Radio className="w-3.5 h-3.5" />
              {wsEnabled
                ? wsStatus === "live"
                  ? `Akumulator WS Aktif${wsMeta ? ` (${wsMeta.accumulatedMin}m)` : ""}`
                  : wsStatus === "connecting"
                  ? "Menghubungkan WS..."
                  : "WS Offline (Mode 5m API)"
                : "Aktifkan Akumulator Live WS"}
            </button>

            <label className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs text-muted bg-paper border border-line cursor-pointer">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="rounded border-lineStrong"
              />
              Auto 60d
            </label>

            <button
              type="button"
              onClick={() => loadForecast({ silent: true, wsPayload: latestWsRef.current })}
              disabled={loading || refreshing}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-medium bg-pine-800 text-paper hover:bg-pine-700 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
        </div>

        {/* Quick coin chips + custom input */}
        <div className="flex flex-wrap items-center gap-1.5">
          {QUICK_SYMBOLS.map((sym) => {
            const active = symbol === sym;
            return (
              <button
                key={sym}
                type="button"
                onClick={() => setSymbol(sym)}
                className={`px-2.5 py-1 rounded-full font-mono text-xs font-medium border transition ${
                  active
                    ? "bg-pine-800 text-paper border-pine-800"
                    : "bg-paper text-muted border-line hover:text-ink hover:border-pine-300"
                }`}
              >
                {sym.replace("USDT", "")}
              </button>
            );
          })}
          {!QUICK_SYMBOLS.includes(symbol) ? (
            <span className="px-2.5 py-1 rounded-full font-mono text-xs font-medium bg-pine-800 text-paper">
              {symbol.replace("USDT", "")}
            </span>
          ) : null}

          <form onSubmit={handleCustomSubmit} className="inline-flex items-center gap-1.5 ml-auto">
            <input
              type="text"
              value={customInput}
              onChange={(e) => setCustomInput(e.target.value)}
              placeholder="Koin lain (mis. PEPE)"
              className="w-36 px-3 py-1 text-xs rounded-full border border-lineStrong bg-paper focus:bg-card focus:outline-none focus:border-pine-500"
            />
            <button
              type="submit"
              className="px-3 py-1 text-xs font-medium rounded-full border border-lineStrong bg-card text-ink hover:border-pine-400"
            >
              Cek
            </button>
          </form>
        </div>

        {/* Horizon & Target/Stop band controls */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2.5 border-t border-line">
          <div className="flex flex-wrap items-center gap-2">
            <span className="micro-label">Batas Waktu ke Depan:</span>
            <div className="inline-flex rounded-full border border-lineStrong bg-paper p-0.5">
              {HORIZON_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setHorizonMin(opt.value)}
                  className={`rounded-full px-3 py-1 font-mono text-[11.5px] transition-colors ${
                    horizonMin === opt.value
                      ? "bg-pine-800 text-paper"
                      : "text-muted hover:text-ink"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="micro-label">Jarak Target vs Stop:</span>
            <select
              value={bandChoice}
              onChange={(e) => setBandChoice(e.target.value)}
              className="px-3 py-1 font-mono text-xs rounded-full border border-lineStrong bg-card text-ink"
            >
              {BAND_OPTIONS.map((b) => (
                <option key={b.value} value={b.value}>
                  {b.label}
                </option>
              ))}
            </select>
          </div>

          {bundle ? (
            <div className="text-[11px] text-muted flex items-center gap-1.5">
              <span
                className={`inline-block w-2 h-2 rounded-full ${
                  bundle.live ? "bg-emerald-500" : "bg-amber-500"
                }`}
              />
              <span>
                Sumber 5m: <strong className="text-ink font-mono">{bundle.source}</strong>
                {bundle.microSource === "browser-ws-accumulator"
                  ? ` + WS ${wsMeta?.accumulatedMin || ""}m (update tiap 15d)`
                  : bundle.microSource
                  ? ` + OB ${bundle.microSource}`
                  : ""}
              </span>
            </div>
          ) : null}
        </div>

        {wsEnabled && wsMessage ? (
          <p className="text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            {wsMessage} Proyeksi tetap berjalan normal menggunakan data candle 5 menit &amp; order
            book server.
          </p>
        ) : null}
        {bundle?.warning ? (
          <p className="text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            {bundle.warning}
          </p>
        ) : null}
      </div>

      {error ? (
        <div className="bg-rose-50 border border-rose-200 text-rose-800 rounded-xl p-4 text-xs">
          {error}
        </div>
      ) : null}

      {loading && !fc ? (
        <div className="card p-10 text-center text-sm text-muted">
          Menghitung distribusi Fokker–Planck {horizonMin} menit ke depan untuk {symbol}...
        </div>
      ) : null}

      {/* HERO DIRECTIONAL VERDICT CARD */}
      {fc ? (
        <div className={`border rounded-2xl p-5 shadow-card space-y-4 ${st.card}`}>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="micro-label">
                  Kesimpulan Arah {bundle.baseAsset}/USDT · {Math.round(fc.horizonMin)} Menit ke
                  Depan
                </span>
                <span className="text-xs font-mono tabular-nums text-ink bg-card border border-line px-2.5 py-0.5 rounded-full">
                  Harga Kini: {fmtPrice(fc.price)}
                </span>
              </div>

              <div className="flex flex-wrap items-center gap-2.5">
                <span
                  className={`inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-sm font-bold border ${st.badge}`}
                >
                  <DirectionIcon shortDir={fc.call.short} className="w-4 h-4" />
                  {fc.call.label}
                </span>

                <span
                  className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border ${
                    fc.consensus?.bothActiveAgree
                      ? "bg-pine-800 text-paper border-pine-800"
                      : "bg-card text-ink border-lineStrong"
                  }`}
                >
                  <ShieldCheck className="w-3.5 h-3.5" />
                  {fc.consensus?.label}
                </span>

                <span className="text-xs text-muted">
                  Tingkat Sinyal: <strong className="text-ink">{st.confidence}</strong>
                </span>
              </div>
            </div>

            <button
              type="button"
              onClick={handleLockCurrent}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full text-xs font-semibold bg-card text-ink border border-lineStrong hover:border-pine-500 shadow-sm"
            >
              <Lock className="w-3.5 h-3.5" />
              Kunci &amp; Buktikan Sinyal {Math.round(fc.horizonMin)}m
            </button>
          </div>

          {/* Tug-of-war Probability Bar: P(Naik) vs P(Turun) */}
          <div className="bg-card/95 border border-line rounded-xl p-4 space-y-2.5">
            <div className="flex flex-wrap items-end justify-between gap-2">
              <div>
                <div className="text-[11px] uppercase tracking-wide text-muted font-medium">
                  Peluang Naik (Harga &gt; Sekarang)
                </div>
                <div className="text-2xl sm:text-3xl font-bold font-mono tabular-nums text-emerald-700">
                  {fmtProb(fc.stats.pUp, 1)}
                </div>
              </div>

              <div className="text-center">
                <div className="text-[11px] text-muted">
                  Pembanding v1 Asli:{" "}
                  <span className="font-mono font-semibold text-ink">
                    {fmtProb(fc.v1Baseline?.pUp, 1)} Naik
                  </span>{" "}
                  ({fc.v1Baseline?.call?.short})
                </div>
                <div className="text-xs font-mono font-semibold text-ink">
                  Selisih {fmtPctPoints((fc.stats.netEdge || 0) * 100, 1)} · Ekspektasi{" "}
                  {fmtPctPoints(fc.stats.expectedMovePct, 2)}
                </div>
              </div>

              <div className="text-right">
                <div className="text-[11px] uppercase tracking-wide text-muted font-medium">
                  Peluang Turun (Harga &lt; Sekarang)
                </div>
                <div className="text-2xl sm:text-3xl font-bold font-mono tabular-nums text-rose-700">
                  {fmtProb(fc.stats.pDown, 1)}
                </div>
              </div>
            </div>

            <div className="relative h-4 rounded-full overflow-hidden flex bg-paper border border-lineStrong">
              <div
                className="bg-emerald-600 h-full transition-all duration-500"
                style={{ width: `${Math.max(4, Math.min(96, fc.stats.pUp * 100))}%` }}
              />
              <div
                className="bg-rose-600 h-full transition-all duration-500"
                style={{ width: `${Math.max(4, Math.min(96, fc.stats.pDown * 100))}%` }}
              />
              <div
                className="absolute inset-y-0 left-1/2 w-0.5 bg-ink/80"
                title="Batas seimbang 50:50"
              />
            </div>

            <div className="pt-1 space-y-1">
              <p className="text-xs font-medium text-ink">{fc.call.action}</p>
              <p className="text-xs text-muted leading-relaxed">{fc.plainSummary}</p>
            </div>
          </div>

          {/* Target vs Stop First-Passage + 5-Point Price Projection */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="bg-card/95 border border-line rounded-xl p-3.5 space-y-2">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="font-semibold text-ink">
                  Adu Cepat Sentuh Batas ±{((fc.targetBand || 0) * 100).toFixed(2)}% Duluan
                </span>
                <span className="text-[11px] text-muted">
                  {isAutoBand ? "Otomatis dari volatilitas" : "Batas manual"}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2.5">
                <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-2.5">
                  <div className="text-[11px] text-emerald-900">
                    Sentuh Target +{fmtProb(fc.targetBand, 2)} Duluan
                  </div>
                  <div className="text-lg font-bold font-mono tabular-nums text-emerald-700">
                    {fmtProb(fc.stats.pTouchTargetFirst, 1)}
                  </div>
                  <div className="text-[11px] font-mono text-muted">
                    {fmtPrice(q?.targetUp)} · Tutup di atas: {fmtProb(fc.stats.pTargetUp, 1)}
                  </div>
                </div>
                <div className="rounded-lg border border-rose-200 bg-rose-50/60 p-2.5">
                  <div className="text-[11px] text-rose-900">
                    Kena Batas Bawah -{fmtProb(fc.targetBand, 2)} Duluan
                  </div>
                  <div className="text-lg font-bold font-mono tabular-nums text-rose-700">
                    {fmtProb(fc.stats.pTouchStopFirst, 1)}
                  </div>
                  <div className="text-[11px] font-mono text-muted">
                    {fmtPrice(q?.stopDown)} · Tutup di bawah: {fmtProb(fc.stats.pStopDown, 1)}
                  </div>
                </div>
              </div>
              <p className="text-[11px] text-muted">
                Peluang tetap tenang di dalam rentang ±{fmtProb(fc.targetBand, 2)} (Zona Stabil):{" "}
                <strong className="font-mono text-ink">{fmtProb(fc.stats.pStayBand, 1)}</strong>.
              </p>
            </div>

            <div className="bg-card/95 border border-line rounded-xl p-3.5 space-y-2">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="font-semibold text-ink">
                  Rentang Proyeksi Harga {Math.round(fc.horizonMin)} Menit Lagi
                </span>
                <span className="text-[11px] text-muted font-mono">
                  Harga Wajar: {fmtPrice(sig?.equilibriumPrice)}
                </span>
              </div>
              <div className="grid grid-cols-5 gap-1.5 text-center">
                <div className="rounded-lg bg-rose-50/70 border border-rose-100 p-2">
                  <div className="text-[10px] text-muted">Bawah P10</div>
                  <div className="text-[11.5px] font-mono font-semibold text-rose-700 mt-0.5">
                    {fmtPrice(q?.p10)}
                  </div>
                </div>
                <div className="rounded-lg bg-paper border border-line p-2">
                  <div className="text-[10px] text-muted">P25</div>
                  <div className="text-[11.5px] font-mono font-medium text-ink mt-0.5">
                    {fmtPrice(q?.p25)}
                  </div>
                </div>
                <div className="rounded-lg bg-pine-800 text-paper p-2">
                  <div className="text-[10px] text-pine-200">Tengah P50</div>
                  <div className="text-[11.5px] font-mono font-bold mt-0.5">
                    {fmtPrice(q?.median)}
                  </div>
                </div>
                <div className="rounded-lg bg-paper border border-line p-2">
                  <div className="text-[10px] text-muted">P75</div>
                  <div className="text-[11.5px] font-mono font-medium text-ink mt-0.5">
                    {fmtPrice(q?.p75)}
                  </div>
                </div>
                <div className="rounded-lg bg-emerald-50/70 border border-emerald-100 p-2">
                  <div className="text-[10px] text-muted">Atas P90</div>
                  <div className="text-[11.5px] font-mono font-semibold text-emerald-700 mt-0.5">
                    {fmtPrice(q?.p90)}
                  </div>
                </div>
              </div>
              <p className="text-[11px] text-muted">
                80% kemungkinan harga {Math.round(fc.horizonMin)} menit lagi berada di{" "}
                <strong className="font-mono text-ink">{fmtPrice(q?.p10)}</strong> s/d{" "}
                <strong className="font-mono text-ink">{fmtPrice(q?.p90)}</strong> (ayunan wajar ±
                {Number(fc.stats.horizonStdPct || 0).toFixed(2)}%).
              </p>
            </div>
          </div>
        </div>
      ) : null}

      {/* MULTI-HORIZON MATRIX: 15m · 30m · 60m */}
      {matrix.length > 0 ? (
        <div className="card p-4 sm:p-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="micro-label">Perbandingan 3 Jangka Waktu Sekaligus (15m · 30m · 60m)</h3>
            <span
              className={`px-3 py-0.5 rounded-full text-xs font-medium border ${
                alignmentDir === "NAIK"
                  ? "bg-emerald-50 text-emerald-800 border-emerald-200"
                  : alignmentDir === "TURUN"
                  ? "bg-rose-50 text-rose-800 border-rose-200"
                  : "bg-paper text-muted border-line"
              }`}
            >
              {alignmentDir === "NAIK"
                ? "15m & 30m Kompak Condong NAIK"
                : alignmentDir === "TURUN"
                ? "15m & 30m Kompak Condong TURUN"
                : "15m & 30m Netral / Belum Searah — Utamakan Sabar"}
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {matrix.map((row) => {
              const rs = callStyles(row.call?.code);
              const selected = row.horizonMin === horizonMin;
              return (
                <button
                  key={row.horizonMin}
                  type="button"
                  onClick={() => setHorizonMin(row.horizonMin)}
                  className={`text-left rounded-xl border p-3.5 transition ${
                    selected
                      ? "border-pine-800 ring-1 ring-pine-800 bg-pine-50/40"
                      : "border-line hover:border-pine-300 bg-card"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <span className="font-mono text-xs font-bold text-ink">
                      {row.horizonMin} Menit ke Depan
                    </span>
                    <span
                      className={`px-2 py-0.5 rounded-full text-[11px] font-semibold border ${rs.badge}`}
                    >
                      {row.call?.label}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs font-mono tabular-nums mb-1">
                    <span className="text-emerald-700 font-semibold">
                      Naik {fmtProb(row.pUp, 1)}
                    </span>
                    <span className="text-rose-700 font-semibold">
                      Turun {fmtProb(row.pDown, 1)}
                    </span>
                  </div>
                  <div className="h-2 rounded-full overflow-hidden flex bg-paper border border-line mb-2">
                    <div
                      className="bg-emerald-600"
                      style={{ width: `${Math.round(row.pUp * 100)}%` }}
                    />
                    <div
                      className="bg-rose-600"
                      style={{ width: `${Math.round(row.pDown * 100)}%` }}
                    />
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-muted">
                    <span className="font-mono">
                      Tengah {fmtPrice(row.priceQuantiles?.median)}
                    </span>
                    <span>{row.consensusLabel}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* FORWARD PDF CURVE + FACTOR BREAKDOWN & 2H CVD */}
      {fc && sig ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="card p-4 sm:p-5 space-y-3">
            <div>
              <h3 className="text-sm font-semibold text-ink">
                Kurva Probabilitas Fokker–Planck ({Math.round(fc.horizonMin)}m: v2 vs v1)
              </h3>
              <p className="text-xs text-muted mt-0.5">
                Jika puncak dan luas kurva bergeser ke kanan garis 0%, peluang naik mengalahkan
                turun. Kesamaan bentuk v1 vs v2:{" "}
                <strong className="font-mono text-ink">
                  {fmtProb(fc.consensus?.pdfOverlap, 1)}
                </strong>
                .
              </p>
            </div>

            <IntradayPdfChart
              overlayCurve={fc.overlayCurve}
              targetBand={fc.targetBand}
              eqShiftPct={sig.eqShiftPct}
              horizonMin={Math.round(fc.horizonMin)}
            />

            <div className="flex flex-wrap items-center gap-4 text-[11px] text-muted pt-2 border-t border-line">
              <span className="inline-flex items-center gap-1.5">
                <span className="w-4 h-0.5 bg-pine-700 inline-block" />
                Model v2 (Multi-Timescale + Anti-Spoof)
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="w-4 h-0.5 border-t-2 border-dashed border-gold-500 inline-block" />
                Model v1 ({fc.v1Baseline?.call?.label})
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-gold-500 inline-block" />
                Harga Wajar ({fmtPctPoints(sig.eqShiftPct, 2)})
              </span>
            </div>

            {Array.isArray(fc.drivers) && fc.drivers.length > 0 ? (
              <ul className="space-y-1.5 pt-2 border-t border-line text-xs text-muted">
                {fc.drivers.map((d, i) => (
                  <li key={i} className="leading-relaxed">
                    • {d}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="card p-4 sm:p-5 space-y-3.5">
            <div>
              <h3 className="text-sm font-semibold text-ink">
                Kenapa Model Menyimpulkan Itu? (Penggerak Rumus)
              </h3>
              <p className="text-xs text-muted mt-0.5">
                Batang ke kanan (hijau) mendorong naik, ke kiri (merah) menekan turun.
              </p>
            </div>

            <div className="space-y-2.5">
              <FactorRow
                label="1. Arus Taker CVD 15 Menit Terakhir"
                value={sig.cvd15m}
                scale={0.35}
                displayText={fmtPctPoints(sig.cvd15m * 100, 1)}
              />
              <FactorRow
                label="2. Arus Taker CVD 30m & Akselerasi"
                value={0.65 * sig.cvd30m + 0.35 * sig.cvdAccel}
                scale={0.32}
                displayText={`Beli ${fmtProb(sig.buyShare30m, 1)} · Aksel ${fmtPctPoints(
                  sig.cvdAccel * 100,
                  1
                )}`}
              />
              <FactorRow
                label="3. Penyerapan Tembok Pasif 15m (Iceberg)"
                value={
                  sig.absorptionSide === "BUY"
                    ? sig.absorptionIndex
                    : sig.absorptionSide === "SELL"
                    ? -sig.absorptionIndex
                    : 0
                }
                scale={0.8}
                displayText={
                  sig.absorptionSide === "NONE"
                    ? "Normal (tidak ada)"
                    : `${sig.absorptionSide === "BUY" ? "Tahan Bawah" : "Tahan Atas"} (${fmtProb(
                        sig.absorptionIndex,
                        0
                      )})`
                }
              />
              <FactorRow
                label="4. Tren Struktur 15m & Tarikan Harga Wajar (VWAP e^−λd)"
                value={0.6 * (sig.trendZ / 2.5) + 0.4 * sig.eqShiftPct}
                scale={0.8}
                displayText={`Tren ${sig.trendZ >= 0 ? "+" : ""}${sig.trendZ}σ · VWAP ${fmtPctPoints(
                  sig.eqShiftPct,
                  2
                )}`}
              />
              {sig.orderbookLive ? (
                <FactorRow
                  label={`5. Order Book / Live WS (meluruh τ=${sig.orderbookLive.tauObMin}m)`}
                  value={0.7 * sig.orderbookLive.tfi + 0.3 * sig.orderbookLive.obi}
                  scale={0.5}
                  displayText={`TFI ${fmtPctPoints(
                    sig.orderbookLive.tfi * 100,
                    0
                  )} · Spoof ${fmtProb(sig.orderbookLive.spoofIndex, 0)}`}
                />
              ) : null}
            </div>

            <div className="pt-2 border-t border-line">
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="font-medium text-ink">
                  Jejak Taker Buy vs Sell (24 Candle 5m = 2 Jam Terakhir)
                </span>
                <span className="text-[11px] font-mono text-muted">
                  Partisipasi: {sig.participationRatio}×
                </span>
              </div>
              <CvdMiniChart series={fc.cvdSeries} />
            </div>
          </div>
        </div>
      ) : null}

      {/* HONEST ACCURACY PROOF: WALK-FORWARD BACKTEST + LIVE LOCK TRACKER */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Proof 1: Walk-Forward Audit on Preceding Closed 5m Candles */}
        <div className="card p-4 sm:p-5 space-y-3">
          <div>
            <h3 className="text-sm font-semibold text-ink flex items-center gap-1.5">
              <Activity className="w-4 h-4 text-pine-700" />
              Bukti 1: Audit Walk-Forward {bt?.horizonMin || horizonMin}m di{" "}
              {bundle?.baseAsset || symbol}
            </h3>
            <p className="text-xs text-muted mt-0.5">
              Menguji rumus persis di {bt?.totalSamples || "—"} titik candle 5 menit sebelumnya
              (~20 jam terakhir) tanpa mengintip masa depan, lalu mencocokkan harga nyata{" "}
              {bt?.horizonMin || horizonMin} menit kemudian.
            </p>
          </div>

          {bt?.ok ? (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <div className="rounded-xl border border-line bg-paper p-2.5">
                  <div className="text-[10px] text-muted">Win-Rate Sinyal v2</div>
                  <div className="text-base font-bold font-mono text-ink mt-0.5">
                    {bt.v2WinRate !== null ? fmtProb(bt.v2WinRate, 1) : "Belum ada"}
                  </div>
                  <div className="text-[10px] text-muted font-mono">
                    {bt.v2ActiveCount} sinyal aktif
                  </div>
                </div>

                <div className="rounded-xl border border-line bg-paper p-2.5">
                  <div className="text-[10px] text-muted">Win-Rate Model v1</div>
                  <div className="text-base font-bold font-mono text-ink mt-0.5">
                    {bt.v1WinRate !== null ? fmtProb(bt.v1WinRate, 1) : "Belum ada"}
                  </div>
                  <div className="text-[10px] text-muted font-mono">
                    {bt.v1ActiveCount} sinyal aktif
                  </div>
                </div>

                <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-2.5">
                  <div className="text-[10px] text-emerald-900">Saat v1 &amp; v2 Sepakat</div>
                  <div className="text-base font-bold font-mono text-emerald-800 mt-0.5">
                    {bt.agreedWinRate !== null ? fmtProb(bt.agreedWinRate, 1) : "—"}
                  </div>
                  <div className="text-[10px] text-emerald-700 font-mono">
                    {bt.agreedActiveCount} sinyal kompak
                  </div>
                </div>

                <div className="rounded-xl border border-amber-200 bg-amber-50/50 p-2.5">
                  <div className="text-[10px] text-amber-900">Pasar 50:50 Disaring</div>
                  <div className="text-base font-bold font-mono text-amber-900 mt-0.5">
                    {bt.v2NeutralSkipped}/{bt.totalSamples}
                  </div>
                  <div className="text-[10px] text-amber-800 font-mono">
                    Brier {Number(bt.brierDirV2).toFixed(3)}
                  </div>
                </div>
              </div>

              <p className="text-[11px] text-muted">
                Kalibrasi rentang ±target: prediksi bertahan{" "}
                <strong className="font-mono text-ink">{fmtProb(bt.bandPredAvg, 1)}</strong> vs
                kejadian nyata{" "}
                <strong className="font-mono text-ink">{fmtProb(bt.bandActualAvg, 1)}</strong>{" "}
                (Brier v2 <span className="font-mono">{Number(bt.brierDirV2).toFixed(4)}</span> vs
                lempar koin <span className="font-mono">0.2500</span>).
              </p>

              {bt.recentSignals?.length > 0 ? (
                <div className="overflow-x-auto border border-line rounded-xl">
                  <table className="w-full text-xs">
                    <thead className="bg-paper text-muted border-b border-line">
                      <tr>
                        <th className="text-left py-1.5 px-2.5 font-medium">Jam</th>
                        <th className="text-left py-1.5 px-2.5 font-medium">Sinyal v2</th>
                        <th className="text-right py-1.5 px-2.5 font-medium">P(Naik)</th>
                        <th className="text-right py-1.5 px-2.5 font-medium">
                          Nyata {bt.horizonMin}m
                        </th>
                        <th className="text-right py-1.5 px-2.5 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line font-mono tabular-nums">
                      {bt.recentSignals.map((r, idx) => (
                        <tr key={`${r.t}-${idx}`}>
                          <td className="py-1.5 px-2.5 text-muted">{fmtUtcTime(r.t)}</td>
                          <td className="py-1.5 px-2.5 font-sans">
                            <span
                              className={
                                r.v2Call === "NAIK"
                                  ? "text-emerald-700 font-semibold"
                                  : r.v2Call === "TURUN"
                                  ? "text-rose-700 font-semibold"
                                  : "text-muted"
                              }
                            >
                              {r.v2Call === "NAIK"
                                ? "Naik"
                                : r.v2Call === "TURUN"
                                ? "Turun"
                                : "Netral"}
                            </span>
                            {r.v1Call === r.v2Call && r.v2Call !== "NETRAL" ? (
                              <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded bg-paper border border-line text-ink">
                                v1+v2
                              </span>
                            ) : null}
                          </td>
                          <td className="py-1.5 px-2.5 text-right text-ink">
                            {fmtProb(r.pUpV2, 1)}
                          </td>
                          <td
                            className={`py-1.5 px-2.5 text-right ${
                              r.realizedPct >= 0 ? "text-emerald-700" : "text-rose-700"
                            }`}
                          >
                            {fmtPctPoints(r.realizedPct, 2)}
                          </td>
                          <td className="py-1.5 px-2.5 text-right font-sans">
                            {r.hit === null || r.hit === undefined ? (
                              <span className="text-muted text-[11px]">Tahan (Skip)</span>
                            ) : r.hit ? (
                              <span className="inline-flex items-center gap-0.5 text-emerald-700 font-semibold">
                                <CheckCircle2 className="w-3.5 h-3.5" /> Tepat
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-0.5 text-rose-600 font-medium">
                                <XCircle className="w-3.5 h-3.5" /> Meleset
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </>
          ) : (
            <p className="text-xs text-muted">Menunggu data audit walk-forward...</p>
          )}
        </div>

        {/* Proof 2: Live Signal Lock & Countdown Scoreboard */}
        <div className="card p-4 sm:p-5 space-y-3">
          <div className="flex items-start justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-ink flex items-center gap-1.5">
                <Clock className="w-4 h-4 text-pine-700" />
                Bukti 2: Uji Sinyal Live Anda (Hitung Mundur Otomatis)
              </h3>
              <p className="text-xs text-muted mt-0.5">
                Tekan <strong>Kunci &amp; Buktikan Sinyal</strong> di kartu atas. Begitu waktu
                15m/30m habis, harga nyata otomatis diambil dan dinilai jujur.
              </p>
            </div>
            {locks.length > 0 ? (
              <button
                type="button"
                onClick={handleClearLocks}
                className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] text-muted hover:text-rose-600 rounded-full border border-line"
              >
                <Trash2 className="w-3 h-3" /> Reset
              </button>
            ) : null}
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div className="rounded-xl border border-line bg-paper p-2.5">
              <div className="text-[10px] text-muted">Win-Rate Live Anda</div>
              <div className="text-base font-bold font-mono text-ink mt-0.5">
                {liveScore.winRate !== null ? fmtProb(liveScore.winRate, 1) : "—"}
              </div>
            </div>
            <div className="rounded-xl border border-line bg-paper p-2.5">
              <div className="text-[10px] text-muted">Tepat / Selesai</div>
              <div className="text-base font-bold font-mono text-emerald-700 mt-0.5">
                {liveScore.hits} / {liveScore.total}
              </div>
            </div>
            <div className="rounded-xl border border-line bg-paper p-2.5">
              <div className="text-[10px] text-muted">Hitung Mundur</div>
              <div className="text-base font-bold font-mono text-amber-700 mt-0.5">
                {liveScore.pending} aktif
              </div>
            </div>
          </div>

          {locks.length === 0 ? (
            <div className="border border-dashed border-lineStrong rounded-xl p-6 text-center">
              <p className="text-xs text-muted leading-relaxed">
                Belum ada sinyal yang dikunci. Saat muncul sinyal{" "}
                <strong className="text-emerald-700">Condong Naik</strong> atau{" "}
                <strong className="text-rose-700">Condong Turun</strong> (terutama bila{" "}
                <strong className="text-ink">v1 &amp; v2 Sepakat</strong>), klik tombol{" "}
                <strong>Kunci &amp; Buktikan Sinyal</strong> untuk membuktikan hasilnya 15–30 menit
                kemudian.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto border border-line rounded-xl max-h-64 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-paper text-muted border-b border-line sticky top-0">
                  <tr>
                    <th className="text-left py-1.5 px-2.5 font-medium">Koin</th>
                    <th className="text-left py-1.5 px-2.5 font-medium">Prediksi</th>
                    <th className="text-right py-1.5 px-2.5 font-medium">Harga Awal</th>
                    <th className="text-right py-1.5 px-2.5 font-medium">Status / Hasil</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line font-mono tabular-nums">
                  {locks.map((l) => {
                    const left = l.dueAt - nowTs;
                    return (
                      <tr key={l.id}>
                        <td className="py-1.5 px-2.5 text-ink font-semibold">
                          {l.baseAsset}{" "}
                          <span className="text-[10px] font-normal text-muted">
                            {l.horizonMin}m
                          </span>
                        </td>
                        <td className="py-1.5 px-2.5 font-sans">
                          <span
                            className={
                              l.shortDir === "NAIK"
                                ? "text-emerald-700 font-semibold"
                                : l.shortDir === "TURUN"
                                ? "text-rose-700 font-semibold"
                                : "text-amber-800 font-medium"
                            }
                          >
                            {l.shortDir}
                          </span>{" "}
                          <span className="text-[10px] font-mono text-muted">
                            ({fmtProb(l.pUp, 0)})
                          </span>
                        </td>
                        <td className="py-1.5 px-2.5 text-right text-ink">
                          {fmtPrice(l.entryPrice)}
                        </td>
                        <td className="py-1.5 px-2.5 text-right font-sans">
                          {l.status === "pending" ? (
                            <span className="font-mono text-amber-800 font-medium">
                              {left > 0 ? `Sisa ${fmtCountdown(left)}` : "Mengecek..."}
                            </span>
                          ) : l.outcome === "HIT" ? (
                            <span className="text-emerald-700 font-semibold">
                              Tepat ({fmtPctPoints(l.realizedPct, 2)})
                            </span>
                          ) : l.outcome === "MISS" ? (
                            <span className="text-rose-600 font-medium">
                              Meleset ({fmtPctPoints(l.realizedPct, 2)})
                            </span>
                          ) : (
                            <span className="text-muted">
                              Netral ({fmtPctPoints(l.realizedPct, 2)})
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
