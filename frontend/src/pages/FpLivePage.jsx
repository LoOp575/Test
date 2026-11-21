import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  Clock,
  Layers,
  Lock,
  Minus,
  Radio,
  RefreshCw,
  ShieldCheck,
  Trash2,
  XCircle,
} from "lucide-react";
import { fetchFpLive, postFpLiveWs } from "../lib/api";
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
  "PEPEUSDT",
  "TONUSDT",
];

const HORIZON_OPTIONS = [
  { value: 5, label: "5 Menit" },
  { value: 15, label: "15 Menit" },
  { value: 30, label: "30 Menit" },
  { value: 60, label: "60 Menit" },
];

const BAND_OPTIONS = [
  { value: "auto", label: "Otomatis (±σ)" },
  { value: "0.0025", label: "±0.25%" },
  { value: "0.004", label: "±0.40%" },
  { value: "0.0065", label: "±0.65%" },
  { value: "0.01", label: "±1.00%" },
  { value: "0.015", label: "±1.50%" },
];

const LOCK_KEY = "meridian_fp_live_locks_v1";

function loadLocks() {
  try {
    const raw = window.localStorage.getItem(LOCK_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.slice(0, 30) : [];
  } catch {
    return [];
  }
}

function saveLocks(arr) {
  try {
    window.localStorage.setItem(LOCK_KEY, JSON.stringify(arr.slice(0, 30)));
  } catch {
    /* ignore storage limits */
  }
}

function fmtPx(v) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
  const n = Number(v);
  if (n >= 1000) {
    return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
  if (n >= 1) return `$${n.toFixed(4)}`;
  if (n >= 0.01) return `$${n.toFixed(5)}`;
  return `$${n.toFixed(7)}`;
}

function fmtQty(v) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
  const n = Number(v);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return n.toLocaleString("en-US", { maximumFractionDigits: 1 });
  if (n >= 10) return n.toFixed(2);
  return n.toFixed(4);
}

function fmtProb(v, digits = 1) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
  return `${(Number(v) * 100).toFixed(digits)}%`;
}

function fmtSignedPct(v, digits = 2) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
  const n = Number(v);
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

function fmtUtc(ms) {
  if (!ms) return "—";
  try {
    return `${new Date(ms).toISOString().slice(11, 16)} UTC`;
  } catch {
    return "—";
  }
}

function fmtTimer(msLeft) {
  const s = Math.max(0, Math.floor(msLeft / 1000));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

function callTheme(tone) {
  if (tone === "up-strong") {
    return {
      card: "border-emerald-300 bg-gradient-to-br from-emerald-50/90 via-card to-card",
      badge: "bg-pine-700 text-white border-pine-800",
    };
  }
  if (tone === "up-mild") {
    return {
      card: "border-emerald-200 bg-gradient-to-br from-emerald-50/50 via-card to-card",
      badge: "bg-emerald-100 text-emerald-900 border-emerald-300",
    };
  }
  if (tone === "down-strong") {
    return {
      card: "border-rose-300 bg-gradient-to-br from-rose-50/90 via-card to-card",
      badge: "bg-rose-700 text-white border-rose-800",
    };
  }
  if (tone === "down-mild") {
    return {
      card: "border-rose-200 bg-gradient-to-br from-rose-50/50 via-card to-card",
      badge: "bg-rose-100 text-rose-900 border-rose-300",
    };
  }
  return {
    card: "border-amber-200 bg-gradient-to-br from-amber-50/45 via-card to-card",
    badge: "bg-amber-100 text-amber-900 border-amber-300",
  };
}

function DirIcon({ shortDir, className = "w-4 h-4" }) {
  if (shortDir === "NAIK") return <ArrowUpRight className={className} />;
  if (shortDir === "TURUN") return <ArrowDownRight className={className} />;
  return <Minus className={className} />;
}

/* ---------- Forward Fokker–Planck Curve (Split Red < 0% | Green > 0%) ---------- */

function ForwardPdfSvg({ overlayCurve = [], targetBand = 0.004, eqShiftPct = 0, horizonMin = 30 }) {
  if (!Array.isArray(overlayCurve) || overlayCurve.length < 3) return null;
  const W = 620;
  const H = 235;
  const pad = { top: 22, right: 18, bottom: 34, left: 18 };
  const pw = W - pad.left - pad.right;
  const ph = H - pad.top - pad.bottom;
  const baseY = pad.top + ph;

  const bandPct = (Number(targetBand) || 0.004) * 100;
  const xs = overlayCurve.map((p) => Number(p.xPct) || 0);
  const minX = Math.min(...xs, -bandPct * 1.6);
  const maxX = Math.max(...xs, bandPct * 1.6);
  const maxY = Math.max(
    1e-6,
    ...overlayCurve.map((p) => Math.max(Number(p.v2) || 0, Number(p.v1) || 0))
  );

  const sx = (x) => pad.left + ((x - minX) / Math.max(1e-9, maxX - minX)) * pw;
  const sy = (y) => pad.top + ph - (y / maxY) * ph;

  const lineV2 = overlayCurve
    .map((p, i) => `${i === 0 ? "M" : "L"}${sx(p.xPct).toFixed(1)},${sy(p.v2).toFixed(1)}`)
    .join(" ");
  const lineV1 = overlayCurve
    .map((p, i) => `${i === 0 ? "M" : "L"}${sx(p.xPct).toFixed(1)},${sy(p.v1).toFixed(1)}`)
    .join(" ");

  const halfArea = (side) => {
    const pts = overlayCurve.filter((p) => (side === "down" ? p.xPct <= 0 : p.xPct >= 0));
    if (pts.length < 2) return "";
    const body = pts.map((p) => `L${sx(p.xPct).toFixed(1)},${sy(p.v2).toFixed(1)}`).join(" ");
    return `M${sx(pts[0].xPct).toFixed(1)},${baseY.toFixed(1)} ${body} L${sx(
      pts[pts.length - 1].xPct
    ).toFixed(1)},${baseY.toFixed(1)} Z`;
  };

  const zeroX = sx(0);
  const stopX = sx(-bandPct);
  const tgtX = sx(bandPct);
  const eqX = sx(Math.max(minX, Math.min(maxX, Number(eqShiftPct) || 0)));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none">
      <path d={halfArea("down")} fill="rgba(179, 64, 47, 0.14)" />
      <path d={halfArea("up")} fill="rgba(20, 67, 56, 0.17)" />
      <line
        x1={stopX}
        x2={stopX}
        y1={pad.top}
        y2={baseY}
        stroke="#B3402F"
        strokeWidth="1"
        strokeDasharray="3 3"
      />
      <line
        x1={tgtX}
        x2={tgtX}
        y1={pad.top}
        y2={baseY}
        stroke="#1F6F4A"
        strokeWidth="1"
        strokeDasharray="3 3"
      />
      <line x1={zeroX} x2={zeroX} y1={pad.top - 4} y2={baseY} stroke="#1F2721" strokeWidth="1.4" />
      <circle cx={eqX} cy={baseY} r="3.8" fill="#B18A44" />
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

/* ---------- 2-Hour Taker CVD Bar + Cumulative Curve ---------- */

function CvdHistorySvg({ series = [] }) {
  if (!Array.isArray(series) || !series.length) return null;
  const W = 560;
  const H = 122;
  const pad = { top: 10, right: 10, bottom: 20, left: 10 };
  const pw = W - pad.left - pad.right;
  const ph = H - pad.top - pad.bottom;
  const midY = pad.top + ph / 2;

  const maxD = Math.max(1e-6, ...series.map((s) => Math.abs(Number(s.delta) || 0)));
  const cums = series.map((s) => Number(s.cvd) || 0);
  const minC = Math.min(...cums);
  const spanC = Math.max(1e-6, Math.max(...cums) - minC);
  const step = pw / series.length;
  const bw = Math.max(3, step * 0.62);

  const line = series
    .map((s, i) => {
      const cx = pad.left + step * (i + 0.5);
      const cy = pad.top + ph - (((Number(s.cvd) || 0) - minC) / spanC) * ph;
      return `${i === 0 ? "M" : "L"}${cx.toFixed(1)},${cy.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none">
      <line x1={pad.left} x2={W - pad.right} y1={midY} y2={midY} stroke="#D9D5CB" strokeDasharray="2 2" />
      {series.map((s, i) => {
        const d = Number(s.delta) || 0;
        const h = (Math.abs(d) / maxD) * (ph * 0.45);
        return (
          <rect
            key={s.t || i}
            x={pad.left + step * i + (step - bw) / 2}
            y={d >= 0 ? midY - h : midY}
            width={bw}
            height={Math.max(1, h)}
            rx="1.2"
            fill={d >= 0 ? "rgba(31, 111, 74, 0.56)" : "rgba(179, 64, 47, 0.54)"}
          />
        );
      })}
      <path d={line} fill="none" stroke="#1F2721" strokeWidth="1.7" />
      <text x={pad.left} y={H - 4} fontSize="9.5" fill="#68716A">
        {fmtUtc(series[0]?.t)}
      </text>
      <text x={W / 2} y={H - 4} textAnchor="middle" fontSize="9.5" fill="#68716A">
        Batang = Delta Taker (2×Buy − Volume) · Garis = CVD kumulatif 2 jam
      </text>
      <text x={W - pad.right} y={H - 4} textAnchor="end" fontSize="9.5" fill="#68716A">
        {fmtUtc(series[series.length - 1]?.t)}
      </text>
    </svg>
  );
}

/* ---------- Single Formula Component Row ---------- */

function ComponentCard({ item }) {
  const sc = Math.max(-1, Math.min(1, Number(item.score) || 0));
  const widthPct = Math.abs(sc) * 50;
  const badgeCls =
    item.stance === "POSITIF"
      ? "bg-emerald-100 text-emerald-900 border-emerald-200"
      : item.stance === "NEGATIF"
      ? "bg-rose-100 text-rose-900 border-rose-200"
      : "bg-paper text-muted border-line";
  const badgeText =
    item.stance === "POSITIF" ? "BELI" : item.stance === "NEGATIF" ? "JUAL" : "DATAR";

  return (
    <div className="rounded-xl border border-line bg-paper/60 p-3 space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-semibold text-ink">{item.name}</span>
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-mono text-muted">{item.metric}</span>
          <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${badgeCls}`}>
            {badgeText}
          </span>
        </div>
      </div>
      <div className="relative h-1.5 rounded-full bg-card border border-line overflow-hidden">
        <div className="absolute inset-y-0 left-1/2 w-px bg-lineStrong" />
        <div
          className={`absolute inset-y-0 rounded-full ${
            sc >= 0 ? "bg-emerald-600" : "bg-rose-600"
          }`}
          style={
            sc >= 0
              ? { left: "50%", width: `${widthPct}%` }
              : { left: `${50 - widthPct}%`, width: `${widthPct}%` }
          }
        />
      </div>
      <p className="text-[11.5px] text-muted leading-snug capitalize">{item.brief}.</p>
    </div>
  );
}

/* ---------- Order-Book Wall Status Pill ---------- */

function WallStatusBadge({ row }) {
  if (row.status === "SPOOF_JAUH") {
    return (
      <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-900 border border-amber-200">
        Spoof −{row.cutPct}%
      </span>
    );
  }
  if (row.status === "AKTIF_DEKAT") {
    return (
      <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-emerald-50 text-emerald-800 border border-emerald-200">
        Aktif
      </span>
    );
  }
  return (
    <span className="px-1.5 py-0.5 rounded text-[10px] text-muted bg-paper border border-line">
      Sedang
    </span>
  );
}

/* ---------- Dedicated FP Live Page (/fp-live) ---------- */

export default function FpLivePage() {
  const { symbol: routeSym } = useParams();
  const navigate = useNavigate();

  const [symbol, setSymbol] = useState(() => {
    const raw = String(routeSym || "BTCUSDT").toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!raw) return "BTCUSDT";
    return raw.endsWith("USDT") ? raw : `${raw}USDT`;
  });
  const [customInput, setCustomInput] = useState("");
  const [horizonMin, setHorizonMin] = useState(30);
  const [bandChoice, setBandChoice] = useState("auto");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const [wsEnabled, setWsEnabled] = useState(false);
  const [wsStatus, setWsStatus] = useState("idle");
  const [wsMsg, setWsMsg] = useState("");
  const [wsMeta, setWsMeta] = useState(null);
  const wsLatestRef = useRef(null);

  const [bundle, setBundle] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const [locks, setLocks] = useState(() => loadLocks());
  const [nowTs, setNowTs] = useState(() => Date.now());
  const gradingRef = useRef(new Set());

  useEffect(() => {
    if (!routeSym) return;
    const raw = String(routeSym).toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!raw) return;
    const clean = raw.endsWith("USDT") ? raw : `${raw}USDT`;
    setSymbol((prev) => (prev === clean ? prev : clean));
  }, [routeSym]);

  const pickSymbol = (sym) => {
    const clean = sym.endsWith("USDT") ? sym : `${sym}USDT`;
    setSymbol(clean);
    navigate(`/fp-live/${clean}`, { replace: true });
  };

  const targetBandNum = bandChoice === "auto" ? null : Number(bandChoice);

  const loadData = useCallback(
    async ({ silent = false, wsPayload = null } = {}) => {
      if (!silent) setLoading(true);
      else setRefreshing(true);
      setError("");
      try {
        const res = wsPayload
          ? await postFpLiveWs(symbol, {
              horizonMin,
              ...(targetBandNum ? { targetBand: targetBandNum } : {}),
              source: "auto",
              tfi: wsPayload.tfi,
              obi: wsPayload.obi,
              spoofIndex: wsPayload.spoofIndex,
              absorptionIndex: wsPayload.absorptionIndex,
              midPrice: wsPayload.midPrice,
              microPrice: wsPayload.microPrice,
              accumulatedMin: wsPayload.accumulatedMin,
            })
          : await fetchFpLive(symbol, {
              horizonMin,
              targetBand: targetBandNum,
              source: "auto",
            });
        setBundle(res);
      } catch (err) {
        setError(err?.message || "Gagal memuat analisis FP Live.");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [symbol, horizonMin, targetBandNum]
  );

  useEffect(() => {
    wsLatestRef.current = null;
    setWsMeta(null);
    loadData({ silent: false });
  }, [loadData]);

  // Calm 60s refresh (disabled while WS accumulator refreshes calmly every 15s)
  useEffect(() => {
    if (!autoRefresh) return undefined;
    if (wsEnabled && wsStatus === "live") return undefined;
    const id = setInterval(() => loadData({ silent: true, wsPayload: wsLatestRef.current }), 60_000);
    return () => clearInterval(id);
  }, [autoRefresh, wsEnabled, wsStatus, loadData]);

  // Rolling 10-minute Live WS Accumulator (depth20 + aggTrade, calm 15s updates)
  useEffect(() => {
    if (!wsEnabled) {
      setWsStatus("idle");
      setWsMsg("");
      setWsMeta(null);
      wsLatestRef.current = null;
      return undefined;
    }
    const stop = connectIntradayWsAccumulator(symbol, {
      emitIntervalMs: 15_000,
      windowMin: 10,
      onStatus: (st, m) => {
        setWsStatus(st);
        setWsMsg(m || "");
      },
      onSummary: (sum) => {
        wsLatestRef.current = sum;
        setWsMeta(sum);
        loadData({ silent: true, wsPayload: sum });
      },
    });
    return () => stop?.();
  }, [wsEnabled, symbol, loadData]);

  // Countdown tick for locked live forecasts
  useEffect(() => {
    if (!locks.some((l) => l.status === "pending")) return undefined;
    const t = setInterval(() => setNowTs(Date.now()), 1000);
    return () => clearInterval(t);
  }, [locks]);

  // Automatic grading at expiration
  useEffect(() => {
    const due = locks.filter(
      (l) => l.status === "pending" && nowTs >= l.dueAt && !gradingRef.current.has(l.id)
    );
    if (!due.length) return;
    due.forEach(async (item) => {
      gradingRef.current.add(item.id);
      try {
        const res = await fetchFpLive(item.symbol, { horizonMin: item.horizonMin });
        const exitPx = Number(res?.forecast?.price);
        if (!Number.isFinite(exitPx) || exitPx <= 0) return;
        const pct = (exitPx / item.entryPrice - 1) * 100;
        let outcome = "NEUTRAL";
        if (item.shortDir === "NAIK") outcome = pct > 0 ? "HIT" : "MISS";
        else if (item.shortDir === "TURUN") outcome = pct < 0 ? "HIT" : "MISS";
        setLocks((prev) => {
          const next = prev.map((x) =>
            x.id === item.id
              ? { ...x, status: "done", exitPrice: exitPx, realizedPct: pct, outcome }
              : x
          );
          saveLocks(next);
          return next;
        });
      } catch {
        /* retry next tick */
      } finally {
        gradingRef.current.delete(item.id);
      }
    });
  }, [locks, nowTs]);

  const handleCustomSubmit = (e) => {
    e.preventDefault();
    const raw = customInput.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!raw) return;
    setCustomInput("");
    pickSymbol(raw);
  };

  const handleLock = () => {
    const fc = bundle?.forecast;
    if (!fc) return;
    const now = Date.now();
    const entry = {
      id: `${bundle.symbol}-${fc.horizonMin}-${now}`,
      symbol: bundle.symbol,
      baseAsset: bundle.baseAsset,
      horizonMin: fc.horizonMin,
      lockedAt: now,
      dueAt: now + fc.horizonMin * 60_000,
      entryPrice: fc.price,
      shortDir: fc.call.short,
      pUp: fc.stats.pUp,
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
    const done = locks.filter(
      (l) => l.status === "done" && (l.outcome === "HIT" || l.outcome === "MISS")
    );
    const hits = done.filter((l) => l.outcome === "HIT").length;
    return {
      total: done.length,
      hits,
      winRate: done.length ? hits / done.length : null,
      pending: locks.filter((l) => l.status === "pending").length,
    };
  }, [locks]);

  const fc = bundle?.forecast;
  const nar = fc?.narrative;
  const kl = nar?.keyLevels;
  const q = fc?.stats?.priceQuantiles;
  const fp = fc?.firstPassage;
  const ob = bundle?.orderbook;
  const bt = bundle?.backtest;
  const matrix = bundle?.horizonMatrix || [];
  const theme = callTheme(fc?.call?.tone);

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-8 md:py-10 space-y-6">
      {/* Page Header */}
      <div className="border-b border-line pb-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="micro-label text-gold-600">
            Mesin Terpisah · Kecenderungan Live 5 · 15 · 30 · 60 Menit (PDE + Order Book Anti-Spoof)
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={`/fokker-planck-v2/${symbol}`}
              className="btn-ghost !py-1.5 !px-3 text-[11.5px]"
            >
              FP v2 · Banding
              <ArrowUpRight size={13} />
            </Link>
            <Link to={`/fokker-planck/${symbol}`} className="btn-ghost !py-1.5 !px-3 text-[11.5px]">
              v1 Asli
              <ArrowUpRight size={13} />
            </Link>
          </div>
        </div>

        <h1 className="mt-2 font-display text-3xl sm:text-4xl font-semibold tracking-tight text-ink">
          FP Live · Proyeksi Arah &amp; Peta Tembok
        </h1>
        <p className="mt-1.5 text-[13.5px] leading-relaxed text-muted max-w-3xl">
          Menggabungkan candle <strong>1 menit</strong> &amp; <strong>5 menit</strong> (arus Taker
          CVD 5m–2j), deteksi penyerapan Iceberg, filter pola gerak <strong>Variance Ratio</strong>,
          harga wajar <code className="font-mono text-[12px]">VWAP e^(−λd)</code> + Micro-Price, dan{" "}
          <strong>100 level tembok order book</strong> ke persamaan Fokker–Planck.
        </p>
      </div>

      {/* Control Bar */}
      <div className="card p-4 sm:p-5 space-y-3.5">
        {/* Coins + custom search */}
        <div className="flex flex-wrap items-center gap-1.5">
          {QUICK_SYMBOLS.map((sym) => (
            <button
              key={sym}
              type="button"
              onClick={() => pickSymbol(sym)}
              className={`px-2.5 py-1 rounded-full font-mono text-xs font-medium border transition ${
                symbol === sym
                  ? "bg-pine-800 text-paper border-pine-800"
                  : "bg-paper text-muted border-line hover:text-ink hover:border-pine-300"
              }`}
            >
              {sym.replace("USDT", "")}
            </button>
          ))}
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
              placeholder="Koin lain (mis. ARB)"
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

        {/* Horizon + Target Band + Live WS controls */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2.5 border-t border-line">
          <div className="flex flex-wrap items-center gap-2">
            <span className="micro-label">Jangka Waktu:</span>
            <div className="inline-flex rounded-full border border-lineStrong bg-paper p-0.5">
              {HORIZON_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setHorizonMin(opt.value)}
                  className={`rounded-full px-3 py-1 font-mono text-[11.5px] transition-colors ${
                    horizonMin === opt.value ? "bg-pine-800 text-paper" : "text-muted hover:text-ink"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="micro-label">Batas Target/Stop:</span>
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
            >
              <Radio className="w-3.5 h-3.5" />
              {wsEnabled
                ? wsStatus === "live"
                  ? `WS Live${wsMeta ? ` (${wsMeta.accumulatedMin}m)` : ""}`
                  : wsStatus === "connecting"
                  ? "Konek WS..."
                  : "WS Offline"
                : "Akumulator WS"}
            </button>

            <label className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs text-muted bg-paper border border-line cursor-pointer">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="rounded border-lineStrong"
              />
              60d
            </label>

            <button
              type="button"
              onClick={() => loadData({ silent: true, wsPayload: wsLatestRef.current })}
              disabled={loading || refreshing}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-medium bg-pine-800 text-paper hover:bg-pine-700 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
        </div>

        {/* Feed status line */}
        {bundle ? (
          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted pt-1">
            <span className="inline-flex items-center gap-1.5">
              <span
                className={`w-2 h-2 rounded-full ${
                  bundle.live ? "bg-emerald-500" : "bg-amber-500"
                }`}
              />
              Feed: 5m <strong className="font-mono text-ink">{bundle.source5m}</strong>{" "}
              <span
                className={`px-1.5 rounded text-[10px] font-semibold ${
                  bundle.sourceNotes?.k5m === "live"
                    ? "bg-emerald-100 text-emerald-900"
                    : "bg-amber-100 text-amber-900"
                }`}
              >
                {bundle.sourceNotes?.k5m === "live" ? "LIVE" : "SEED"}
              </span>{" "}
              · 1m{" "}
              {bundle.sourceNotes?.k1m === "live" ? (
                <span className="px-1.5 rounded text-[10px] font-semibold bg-emerald-100 text-emerald-900">
                  LIVE
                </span>
              ) : (
                <span
                  className="px-1.5 rounded text-[10px] font-semibold bg-amber-100 text-amber-900"
                  title="Candle 1 menit tidak terjangkau — diinterpolasi presisi dari candle 5 menit (bukan data live)."
                >
                  SINTETIS 5m
                </span>
              )}{" "}
              · Book{" "}
              <strong className="font-mono text-ink">{bundle.sourceBook}</strong>{" "}
              <span
                className={`px-1.5 rounded text-[10px] font-semibold ${
                  bundle.sourceNotes?.book === "live"
                    ? "bg-emerald-100 text-emerald-900"
                    : "bg-amber-100 text-amber-900"
                }`}
              >
                {bundle.sourceNotes?.book === "live" ? "LIVE" : "SEED"}
              </span>{" "}
              ({ob?.levelsCount || 0} level)
            </span>
            <span className="font-mono">
              σ 1m: {fc?.signals?.sigma1mBps} bps · VR: {fc?.regime?.vr} ({fc?.regime?.label})
            </span>
          </div>
        ) : null}

        {wsEnabled && wsMsg ? (
          <p className="text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5">
            {wsMsg}
          </p>
        ) : null}
        {bundle?.warning ? (
          <p className="text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5">
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
        <div className="card p-12 text-center text-sm text-muted">
          Membedah arus 1m/5m, peta tembok order book, dan PDE Fokker–Planck {horizonMin}m untuk{" "}
          {symbol}...
        </div>
      ) : null}

      {/* 1. HERO CARD: ROUNDED CONCLUSION + DATA SYNTHESIS + KEY PRICE LEVELS */}
      {fc && nar ? (
        <div className={`border rounded-2xl p-5 sm:p-6 shadow-card space-y-4 ${theme.card}`}>
          {/* Top badges + Lock button */}
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`inline-flex items-center gap-1.5 px-3.5 py-1 rounded-full text-xs font-bold border ${theme.badge}`}
                >
                  <DirIcon shortDir={fc.call.short} className="w-3.5 h-3.5" />
                  {fc.call.label}
                </span>
                <span className="px-3 py-1 rounded-full text-xs font-mono font-semibold bg-card text-ink border border-lineStrong">
                  {nar.scoreSummary}
                </span>
                <span
                  className={`inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-medium border ${
                    fc.consensus?.bothActiveAgree
                      ? "bg-pine-800 text-paper border-pine-800"
                      : "bg-card text-muted border-line"
                  }`}
                >
                  <ShieldCheck className="w-3.5 h-3.5" />
                  {fc.consensus?.label}
                </span>
                <span className="px-2.5 py-1 rounded-full text-xs font-mono bg-card text-ink border border-line">
                  Spot {fmtPx(fc.price)}
                </span>
              </div>

              <h2 className="font-display text-xl sm:text-2xl font-semibold text-ink leading-snug">
                {nar.headline}
              </h2>
            </div>

            <button
              type="button"
              onClick={handleLock}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full text-xs font-semibold bg-card text-ink border border-lineStrong hover:border-pine-500 shadow-sm"
            >
              <Lock className="w-3.5 h-3.5" />
              Kunci Sinyal {fc.horizonMin}m
            </button>
          </div>

          {/* Probability Tug-of-War + Rounded Analyst Paragraphs */}
          <div className="bg-card/95 border border-line rounded-xl p-4 sm:p-5 space-y-3">
            <div className="flex flex-wrap items-end justify-between gap-2">
              <div>
                <div className="text-[11px] uppercase tracking-wide text-muted font-medium">
                  Peluang Naik ({fc.horizonMin}m)
                </div>
                <div className="text-2xl sm:text-3xl font-bold font-mono tabular-nums text-emerald-700">
                  {fmtProb(fc.stats.pUp, 1)}
                </div>
              </div>

              <div className="text-center">
                <div className="text-[11px] text-muted">
                  Selisih Bersih{" "}
                  <strong className="font-mono text-ink">
                    {fmtSignedPct((fc.stats.netEdge || 0) * 100, 1)}
                  </strong>{" "}
                  · v1:{" "}
                  <span className="font-mono">{fmtProb(fc.v1Baseline?.pUp, 1)} Naik</span>
                </div>
                <div className="text-xs font-mono text-muted">
                  Harga Wajar VWAP {fmtPx(fc.vwapPrice)} · Micro {fmtPx(fc.microPrice)}
                </div>
              </div>

              <div className="text-right">
                <div className="text-[11px] uppercase tracking-wide text-muted font-medium">
                  Peluang Turun ({fc.horizonMin}m)
                </div>
                <div className="text-2xl sm:text-3xl font-bold font-mono tabular-nums text-rose-700">
                  {fmtProb(fc.stats.pDown, 1)}
                </div>
              </div>
            </div>

            <div className="relative h-3.5 rounded-full overflow-hidden flex bg-paper border border-lineStrong">
              <div
                className="bg-emerald-600 h-full transition-all duration-500"
                style={{ width: `${Math.max(4, Math.min(96, fc.stats.pUp * 100))}%` }}
              />
              <div
                className="bg-rose-600 h-full transition-all duration-500"
                style={{ width: `${Math.max(4, Math.min(96, fc.stats.pDown * 100))}%` }}
              />
              <div className="absolute inset-y-0 left-1/2 w-0.5 bg-ink/80" />
            </div>

            {/* Rounded Data Review (Thesis + Opposing/Wait Levels) */}
            <div className="space-y-2 pt-1 text-[13px] leading-relaxed">
              <p className="text-ink">{nar.mainThesis}</p>
              <p className="text-ink/90 bg-paper/80 border border-line rounded-lg px-3.5 py-2.5">
                {nar.counterAndWait}
              </p>
              {nar.extraNotes?.length > 0 ? (
                <div className="space-y-1 pt-0.5">
                  {nar.extraNotes.map((note, idx) => (
                    <p
                      key={idx}
                      className="text-xs text-amber-950 bg-amber-50/75 border border-amber-200 rounded-lg px-3 py-1.5"
                    >
                      {note}
                    </p>
                  ))}
                </div>
              ) : null}
            </div>

            {/* 4 Data-Derived Key Price Boxes */}
            {kl ? (
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5 pt-1">
                <div className="rounded-xl border border-line bg-paper p-3">
                  <div className="text-[10.5px] text-muted">{kl.waitLabel}</div>
                  <div className="text-xs sm:text-[13px] font-mono font-bold text-ink mt-0.5">
                    {kl.waitRange}
                  </div>
                </div>
                <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-3">
                  <div className="text-[10.5px] text-emerald-900">{kl.targetLabel}</div>
                  <div className="text-xs sm:text-[13px] font-mono font-bold text-emerald-800 mt-0.5">
                    {kl.targetRange}
                  </div>
                </div>
                <div className="rounded-xl border border-line bg-paper p-3">
                  <div className="text-[10.5px] text-muted">{kl.triggerLabel}</div>
                  <div className="text-xs sm:text-[13px] font-mono font-bold text-ink mt-0.5">
                    {kl.triggerPrice}
                  </div>
                </div>
                <div className="rounded-xl border border-rose-200 bg-rose-50/50 p-3">
                  <div className="text-[10.5px] text-rose-900">{kl.invalidationLabel}</div>
                  <div className="text-xs sm:text-[13px] font-mono font-bold text-rose-800 mt-0.5">
                    {kl.invalidationPrice}
                  </div>
                </div>
              </div>
            ) : null}
          </div>

          {/* Exact Absorbing-Barrier 3-Way Odds + P10..P90 Price Quantiles */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="bg-card/95 border border-line rounded-xl p-3.5 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-ink">
                  Adu Cepat Batas Serap ±{((fc.targetBand || 0) * 100).toFixed(2)}% (Total 100%)
                </span>
                <span className="text-[11px] text-muted font-mono">PDE Absorbing Barrier</span>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-2.5">
                  <div className="text-[10px] text-emerald-900">Target Atas Duluan</div>
                  <div className="text-base sm:text-lg font-bold font-mono text-emerald-700">
                    {fmtProb(fp?.pHitTargetFirst, 1)}
                  </div>
                  <div className="text-[10.5px] font-mono text-muted">{fmtPx(q?.targetUp)}</div>
                </div>
                <div className="rounded-lg border border-rose-200 bg-rose-50/60 p-2.5">
                  <div className="text-[10px] text-rose-900">Batas Bawah Duluan</div>
                  <div className="text-base sm:text-lg font-bold font-mono text-rose-700">
                    {fmtProb(fp?.pHitStopFirst, 1)}
                  </div>
                  <div className="text-[10.5px] font-mono text-muted">{fmtPx(q?.stopDown)}</div>
                </div>
                <div className="rounded-lg border border-line bg-paper p-2.5">
                  <div className="text-[10px] text-muted">Belum Kena ({fc.horizonMin}m)</div>
                  <div className="text-base sm:text-lg font-bold font-mono text-ink">
                    {fmtProb(fp?.pNeitherYet, 1)}
                  </div>
                  <div className="text-[10.5px] font-mono text-muted">Di dalam rentang</div>
                </div>
              </div>
            </div>

            <div className="bg-card/95 border border-line rounded-xl p-3.5 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-ink">
                  Sebaran Harga {fc.horizonMin} Menit Lagi (P10 – P90 = Cakupan 80%)
                </span>
                <span className="text-[11px] font-mono text-muted">
                  σ ±{Number(fc.stats.horizonStdPct || 0).toFixed(2)}%
                </span>
              </div>
              <div className="grid grid-cols-5 gap-1.5 text-center">
                <div className="rounded-lg bg-rose-50/70 border border-rose-100 p-2">
                  <div className="text-[10px] text-muted">P10</div>
                  <div className="text-[11px] font-mono font-semibold text-rose-700 mt-0.5">
                    {fmtPx(q?.p10)}
                  </div>
                </div>
                <div className="rounded-lg bg-paper border border-line p-2">
                  <div className="text-[10px] text-muted">P25</div>
                  <div className="text-[11px] font-mono text-ink mt-0.5">{fmtPx(q?.p25)}</div>
                </div>
                <div className="rounded-lg bg-pine-800 text-paper p-2">
                  <div className="text-[10px] text-pine-200">Tengah</div>
                  <div className="text-[11px] font-mono font-bold mt-0.5">{fmtPx(q?.median)}</div>
                </div>
                <div className="rounded-lg bg-paper border border-line p-2">
                  <div className="text-[10px] text-muted">P75</div>
                  <div className="text-[11px] font-mono text-ink mt-0.5">{fmtPx(q?.p75)}</div>
                </div>
                <div className="rounded-lg bg-emerald-50/70 border border-emerald-100 p-2">
                  <div className="text-[10px] text-muted">P90</div>
                  <div className="text-[11px] font-mono font-semibold text-emerald-700 mt-0.5">
                    {fmtPx(q?.p90)}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {/* 2. 4-HORIZON MATRIX: 5m · 15m · 30m · 60m */}
      {matrix.length > 0 ? (
        <div className="card p-4 sm:p-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="micro-label">Perbandingan 4 Jangka Waktu (5m · 15m · 30m · 60m)</h3>
            <span
              className={`px-3 py-0.5 rounded-full text-xs font-medium border ${
                bundle?.aligned15m30m === "NAIK"
                  ? "bg-emerald-50 text-emerald-800 border-emerald-200"
                  : bundle?.aligned15m30m === "TURUN"
                  ? "bg-rose-50 text-rose-800 border-rose-200"
                  : "bg-paper text-muted border-line"
              }`}
            >
              {bundle?.aligned15m30m
                ? `15m & 30m Searah: ${bundle.aligned15m30m}`
                : "15m & 30m Imbang / Beda Arah"}
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {matrix.map((m) => {
              const mt = callTheme(m.call?.tone);
              const active = m.horizonMin === horizonMin;
              return (
                <button
                  key={m.horizonMin}
                  type="button"
                  onClick={() => setHorizonMin(m.horizonMin)}
                  className={`text-left rounded-xl border p-3.5 transition ${
                    active
                      ? "border-pine-800 ring-1 ring-pine-800 bg-pine-50/40"
                      : "border-line hover:border-pine-300 bg-card"
                  }`}
                >
                  <div className="flex items-center justify-between gap-1.5 mb-2">
                    <span className="font-mono text-xs font-bold text-ink">{m.horizonMin} Menit</span>
                    <span
                      className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${mt.badge}`}
                    >
                      {m.call?.short} {Math.round(Math.max(m.pUp, m.pDown) * 100)}%
                    </span>
                  </div>
                  <div className="flex justify-between text-[11px] font-mono tabular-nums mb-1">
                    <span className="text-emerald-700 font-semibold">Naik {fmtProb(m.pUp, 1)}</span>
                    <span className="text-rose-700 font-semibold">Turun {fmtProb(m.pDown, 1)}</span>
                  </div>
                  <div className="h-1.5 rounded-full overflow-hidden flex bg-paper border border-line mb-2">
                    <div className="bg-emerald-600" style={{ width: `${Math.round(m.pUp * 100)}%` }} />
                    <div className="bg-rose-600" style={{ width: `${Math.round(m.pDown * 100)}%` }} />
                  </div>
                  <div className="text-[10.5px] font-mono text-muted truncate">
                    {fmtPx(m.rangeLow)} – {fmtPx(m.rangeHigh)}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* 3. 5-COMPONENT SCORECARD + PDE CURVE & 2H CVD */}
      {fc ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="card p-4 sm:p-5 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-ink">
                Rincian 5 Komponen Rumus ({bundle.baseAsset})
              </h3>
              <span className="text-xs font-mono text-muted">{nar?.scoreSummary}</span>
            </div>
            <div className="space-y-2">
              {(fc.components || []).map((c) => (
                <ComponentCard key={c.id} item={c} />
              ))}
            </div>
          </div>

          <div className="card p-4 sm:p-5 space-y-4">
            <div className="space-y-2">
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="text-sm font-semibold text-ink">
                  Kurva PDE Fokker–Planck ({fc.horizonMin}m: FP Live vs v1)
                </h3>
                <span className="text-[11px] font-mono text-muted">
                  Irisan v1: {fmtProb(fc.consensus?.pdfOverlap, 0)}
                </span>
              </div>
              <ForwardPdfSvg
                overlayCurve={fc.overlayCurve}
                targetBand={fc.targetBand}
                eqShiftPct={fc.eqShiftPct}
                horizonMin={fc.horizonMin}
              />
              <div className="flex flex-wrap items-center gap-4 text-[11px] text-muted">
                <span className="inline-flex items-center gap-1.5">
                  <span className="w-4 h-0.5 bg-pine-700 inline-block" /> FP Live
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="w-4 h-0.5 border-t-2 border-dashed border-gold-500 inline-block" />{" "}
                  v1 ({fc.v1Baseline?.call?.short})
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-full bg-gold-500 inline-block" /> Harga Wajar
                  ({fmtSignedPct(fc.eqShiftPct, 2)})
                </span>
              </div>
            </div>

            <div className="pt-3 border-t border-line space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-ink">Arus Taker Delta &amp; CVD Kumulatif (2 Jam / 5m)</span>
                <span className="font-mono text-[11px] text-muted">
                  Beli 5m {fmtProb(fc.signals?.buyShare5m, 0)} · 15m{" "}
                  {fmtProb(fc.signals?.buyShare15m, 0)} · 30m {fmtProb(fc.signals?.buyShare30m, 0)}{" "}
                  · 2j {fmtProb(fc.signals?.buyShare2h, 0)}
                </span>
              </div>
              <CvdHistorySvg series={fc.cvdSeries} />
            </div>
          </div>
        </div>
      ) : null}

      {/* 4. LIVE ORDER-BOOK WALL MAP (TOP 5 SUPPORT BID & TOP 5 RESISTANCE ASK) */}
      {ob ? (
        <div className="card p-4 sm:p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-ink flex items-center gap-1.5">
                <Layers className="w-4 h-4 text-pine-700" />
                Peta Tembok Order Book &amp; Filter Spoof e^(−λd)
              </h3>
              <p className="text-xs text-muted mt-0.5">
                Membandingkan antrean mentah (OBI{" "}
                <span className="font-mono">{fmtSignedPct((ob.rawObi || 0) * 100, 1)}</span>) vs
                antrean bersih sesudah tembok jauh dipotong λ={ob.decayLambda} (OBI{" "}
                <strong className="font-mono text-ink">
                  {fmtSignedPct((ob.weightedObi || 0) * 100, 1)}
                </strong>
                , spread <span className="font-mono">{ob.spreadBps} bps</span>, Micro-shift{" "}
                <span className="font-mono">{ob.microShiftBps >= 0 ? "+" : ""}{ob.microShiftBps} bps</span>
                ).
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Top 5 Supports */}
            <div className="border border-line rounded-xl overflow-hidden">
              <div className="bg-emerald-50/70 border-b border-line px-3 py-2 flex items-center justify-between text-xs">
                <span className="font-semibold text-emerald-900">
                  Top 5 Tembok Beli (Support Bid)
                </span>
                {ob.activeBidWall ? (
                  <span className="font-mono text-[11px] text-emerald-800">
                    Utama: {fmtPx(ob.activeBidWall.price)} (−{ob.activeBidWall.distPct}%)
                  </span>
                ) : null}
              </div>
              <table className="w-full text-xs">
                <thead className="bg-paper text-muted border-b border-line">
                  <tr>
                    <th className="text-left py-1.5 px-2.5 font-medium">Harga Bid</th>
                    <th className="text-right py-1.5 px-2.5 font-medium">
                      Jumlah ({ob?.qtyUnit || bundle.baseAsset})
                    </th>
                    <th className="text-right py-1.5 px-2.5 font-medium">Porsi / Jarak</th>
                    <th className="text-right py-1.5 px-2.5 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line font-mono tabular-nums">
                  {(ob.topSupports || []).map((r, i) => (
                    <tr key={`b-${i}`}>
                      <td className="py-1.5 px-2.5 text-emerald-800 font-semibold">
                        {fmtPx(r.price)}
                      </td>
                      <td className="py-1.5 px-2.5 text-right text-ink">{fmtQty(r.qty)}</td>
                      <td className="py-1.5 px-2.5 text-right text-muted">
                        {r.sharePct}% · −{r.distPct}%
                      </td>
                      <td className="py-1.5 px-2.5 text-right font-sans">
                        <WallStatusBadge row={r} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Top 5 Resistances */}
            <div className="border border-line rounded-xl overflow-hidden">
              <div className="bg-rose-50/70 border-b border-line px-3 py-2 flex items-center justify-between text-xs">
                <span className="font-semibold text-rose-900">
                  Top 5 Tembok Jual (Resistance Ask)
                </span>
                {ob.activeAskWall ? (
                  <span className="font-mono text-[11px] text-rose-800">
                    Utama: {fmtPx(ob.activeAskWall.price)} (+{ob.activeAskWall.distPct}%)
                  </span>
                ) : null}
              </div>
              <table className="w-full text-xs">
                <thead className="bg-paper text-muted border-b border-line">
                  <tr>
                    <th className="text-left py-1.5 px-2.5 font-medium">Harga Ask</th>
                    <th className="text-right py-1.5 px-2.5 font-medium">
                      Jumlah ({ob?.qtyUnit || bundle.baseAsset})
                    </th>
                    <th className="text-right py-1.5 px-2.5 font-medium">Porsi / Jarak</th>
                    <th className="text-right py-1.5 px-2.5 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line font-mono tabular-nums">
                  {(ob.topResistances || []).map((r, i) => (
                    <tr key={`a-${i}`}>
                      <td className="py-1.5 px-2.5 text-rose-800 font-semibold">
                        {fmtPx(r.price)}
                      </td>
                      <td className="py-1.5 px-2.5 text-right text-ink">{fmtQty(r.qty)}</td>
                      <td className="py-1.5 px-2.5 text-right text-muted">
                        {r.sharePct}% · +{r.distPct}%
                      </td>
                      <td className="py-1.5 px-2.5 text-right font-sans">
                        <WallStatusBadge row={r} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ) : null}

      {/* 5. ACCURACY SCOREBOARD: WALK-FORWARD 20H + LIVE LOCK TRACKER */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-4 sm:p-5 space-y-3">
          <div>
            <h3 className="text-sm font-semibold text-ink flex items-center gap-1.5">
              <Activity className="w-4 h-4 text-pine-700" />
              Rekam Akurasi Walk-Forward {bt?.horizonMin || horizonMin}m (~20 Jam Terakhir)
            </h3>
            <p className="text-xs text-muted mt-0.5">
              Diuji berurutan di {bt?.totalSamples || "—"} titik candle tertutup tanpa mengintip
              harga masa depan (sekaligus menjadi kalibrasi otomatis).
            </p>
          </div>

          {bt?.ok ? (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <div className="rounded-xl border border-line bg-paper p-2.5">
                  <div className="text-[10px] text-muted">Win-Rate FP Live</div>
                  <div className="text-base font-bold font-mono text-ink mt-0.5">
                    {bt.winRate !== null ? fmtProb(bt.winRate, 1) : "—"}
                  </div>
                  <div className="text-[10px] font-mono text-muted">
                    {bt.activeHits}/{bt.activeCount} sinyal
                  </div>
                </div>
                <div className="rounded-xl border border-line bg-paper p-2.5">
                  <div className="text-[10px] text-muted">Pembanding v1</div>
                  <div className="text-base font-bold font-mono text-ink mt-0.5">
                    {bt.v1WinRate !== null ? fmtProb(bt.v1WinRate, 1) : "—"}
                  </div>
                  <div className="text-[10px] font-mono text-muted">
                    {bt.v1ActiveCount} sinyal
                  </div>
                </div>
                <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-2.5">
                  <div className="text-[10px] text-emerald-900">v1 &amp; FP Live Sepakat</div>
                  <div className="text-base font-bold font-mono text-emerald-800 mt-0.5">
                    {bt.agreedWinRate !== null ? fmtProb(bt.agreedWinRate, 1) : "—"}
                  </div>
                  <div className="text-[10px] font-mono text-emerald-700">
                    {bt.agreedHits}/{bt.agreedCount} kompak
                  </div>
                </div>
                <div className="rounded-xl border border-amber-200 bg-amber-50/50 p-2.5">
                  <div className="text-[10px] text-amber-900">Imbang Disaring</div>
                  <div className="text-base font-bold font-mono text-amber-900 mt-0.5">
                    {bt.neutralSkipped}/{bt.totalSamples}
                  </div>
                  <div className="text-[10px] font-mono text-amber-800">
                    Brier {Number(bt.brier).toFixed(3)}
                  </div>
                </div>
              </div>

              {bt.recentSignals?.length > 0 ? (
                <div className="overflow-x-auto border border-line rounded-xl">
                  <table className="w-full text-xs">
                    <thead className="bg-paper text-muted border-b border-line">
                      <tr>
                        <th className="text-left py-1.5 px-2.5 font-medium">Jam</th>
                        <th className="text-left py-1.5 px-2.5 font-medium">Kesimpulan</th>
                        <th className="text-right py-1.5 px-2.5 font-medium">P(Naik)</th>
                        <th className="text-right py-1.5 px-2.5 font-medium">
                          Realisasi {bt.horizonMin}m
                        </th>
                        <th className="text-right py-1.5 px-2.5 font-medium">Hasil</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line font-mono tabular-nums">
                      {bt.recentSignals.map((r, i) => (
                        <tr key={`${r.t}-${i}`}>
                          <td className="py-1.5 px-2.5 text-muted">{fmtUtc(r.t)}</td>
                          <td className="py-1.5 px-2.5 font-sans">
                            <span
                              className={
                                r.call === "NAIK"
                                  ? "text-emerald-700 font-semibold"
                                  : r.call === "TURUN"
                                  ? "text-rose-700 font-semibold"
                                  : "text-muted"
                              }
                            >
                              {r.call}
                            </span>
                          </td>
                          <td className="py-1.5 px-2.5 text-right text-ink">
                            {fmtProb(r.pUp, 1)}
                          </td>
                          <td
                            className={`py-1.5 px-2.5 text-right ${
                              r.realizedPct >= 0 ? "text-emerald-700" : "text-rose-700"
                            }`}
                          >
                            {fmtSignedPct(r.realizedPct, 2)}
                          </td>
                          <td className="py-1.5 px-2.5 text-right font-sans">
                            {r.hit === null || r.hit === undefined ? (
                              <span className="text-muted text-[11px]">Disaring</span>
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
            <p className="text-xs text-muted">Memuat audit walk-forward...</p>
          )}
        </div>

        {/* Live Signal Lock Scoreboard */}
        <div className="card p-4 sm:p-5 space-y-3">
          <div className="flex items-start justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-ink flex items-center gap-1.5">
                <Clock className="w-4 h-4 text-pine-700" />
                Catatan Kunci Sinyal Live (Hitung Mundur Nyata)
              </h3>
              <p className="text-xs text-muted mt-0.5">
                Mencatat harga saat tombol <strong>Kunci Sinyal</strong> ditekan dan mencocokkan
                otomatis saat waktu 5m/15m/30m/60m selesai.
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
              <div className="text-[10px] text-muted">Win-Rate Live</div>
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
              <div className="text-[10px] text-muted">Sedang Jalan</div>
              <div className="text-base font-bold font-mono text-amber-700 mt-0.5">
                {liveScore.pending} sinyal
              </div>
            </div>
          </div>

          {locks.length === 0 ? (
            <div className="border border-dashed border-lineStrong rounded-xl p-6 text-center text-xs text-muted">
              Belum ada sinyal terkunci. Tekan <strong>Kunci Sinyal</strong> di kartu atas untuk
              merekam dan menguji akurasi real-time di koin pilihanmu.
            </div>
          ) : (
            <div className="overflow-x-auto border border-line rounded-xl max-h-64 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-paper text-muted border-b border-line sticky top-0">
                  <tr>
                    <th className="text-left py-1.5 px-2.5 font-medium">Koin</th>
                    <th className="text-left py-1.5 px-2.5 font-medium">Arah</th>
                    <th className="text-right py-1.5 px-2.5 font-medium">Harga Kunci</th>
                    <th className="text-right py-1.5 px-2.5 font-medium">Status / Hasil</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line font-mono tabular-nums">
                  {locks.map((l) => {
                    const left = l.dueAt - nowTs;
                    return (
                      <tr key={l.id}>
                        <td className="py-1.5 px-2.5 font-semibold text-ink">
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
                                : "text-amber-800"
                            }
                          >
                            {l.shortDir}
                          </span>{" "}
                          <span className="text-[10px] font-mono text-muted">
                            ({fmtProb(l.pUp, 0)})
                          </span>
                        </td>
                        <td className="py-1.5 px-2.5 text-right text-ink">
                          {fmtPx(l.entryPrice)}
                        </td>
                        <td className="py-1.5 px-2.5 text-right font-sans">
                          {l.status === "pending" ? (
                            <span className="font-mono text-amber-800 font-medium">
                              {left > 0 ? `Sisa ${fmtTimer(left)}` : "Mengecek..."}
                            </span>
                          ) : l.outcome === "HIT" ? (
                            <span className="text-emerald-700 font-semibold">
                              Tepat ({fmtSignedPct(l.realizedPct, 2)})
                            </span>
                          ) : l.outcome === "MISS" ? (
                            <span className="text-rose-600 font-medium">
                              Meleset ({fmtSignedPct(l.realizedPct, 2)})
                            </span>
                          ) : (
                            <span className="text-muted">
                              Imbang ({fmtSignedPct(l.realizedPct, 2)})
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
