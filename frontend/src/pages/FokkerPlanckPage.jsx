import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  ArrowUpDown,
  BookOpen,
  CheckCircle2,
  FlaskConical,
  Info,
  Layers,
  Play,
  Radio,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sliders,
  WifiOff,
} from "lucide-react";
import StatTile from "../components/StatTile";
import VerdictBadge from "../components/VerdictBadge";
import {
  fetchFpAsset,
  fetchFpSpec,
  fetchFpUniverse,
  runFpBacktest,
  simulateFp,
} from "../lib/api";
import { fmtPct, fmtPrice, fmtUsdCompact } from "../lib/format";
import { subscribeOrderbookStream } from "../lib/liveOrderbook";
import { useLiveTickers } from "../lib/useLiveTickers";

const TABS = [
  { id: "universe", label: "01 · Peta Universe", icon: Layers },
  { id: "asset", label: "02 · Distribusi Aset", icon: Activity },
  { id: "spoofing", label: "03 · Spoofing & Lab PDE", icon: Sliders },
  { id: "backtest", label: "04 · Validasi & Rumus", icon: FlaskConical },
];

const HORIZON_OPTIONS = [
  { value: 1, label: "1 Hari" },
  { value: 3, label: "3 Hari" },
  { value: 7, label: "7 Hari" },
  { value: 14, label: "14 Hari" },
  { value: 30, label: "30 Hari" },
];

const BAND_OPTIONS = [
  { value: 0.03, label: "±3%" },
  { value: 0.05, label: "±5%" },
  { value: 0.08, label: "±8%" },
  { value: 0.1, label: "±10%" },
];

const REGIME_FILTERS = [
  { id: "ALL", label: "Semua" },
  { id: "BULL", label: "Akumulasi / Naik" },
  { id: "CALM", label: "Sideways Tenang" },
  { id: "VOL", label: "Dispersi Tinggi" },
  { id: "RISK", label: "Downside / Crash" },
];

const DIRECT_PRESETS = [
  {
    id: "initial_spoof",
    label: "Diskusi Awal (μ=-0.35, D=0.05)",
    sub: "Tekanan jual kuat, dt=1",
    values: {
      mu: -0.35,
      diffusion: 0.05,
      kappa: 0.0,
      tailEta: 0.0,
      downsideAsym: 0.0,
      horizon: 1.0,
      stableBand: 0.1,
      naiveP0: 0.8,
    },
  },
  {
    id: "vol_explosion",
    label: "Ledakan Volatilitas (μ=0, D=0.50)",
    sub: "Buktikan rumus naif terbalik",
    values: {
      mu: 0.0,
      diffusion: 0.5,
      kappa: 0.0,
      tailEta: 0.25,
      downsideAsym: 0.2,
      horizon: 1.0,
      stableBand: 0.1,
      naiveP0: 0.8,
    },
  },
  {
    id: "strong_pump",
    label: "Pump Kuat (μ=+0.35, D=0.10)",
    sub: "Rumus naif tembus >100%",
    values: {
      mu: 0.35,
      diffusion: 0.1,
      kappa: 0.0,
      tailEta: 0.15,
      downsideAsym: 0.0,
      horizon: 1.0,
      stableBand: 0.1,
      naiveP0: 0.8,
    },
  },
  {
    id: "fat_tail_crypto",
    label: "Ekor Tebal Crypto (OU + Fat-Tail)",
    sub: "Mean-reversion + Student-t",
    values: {
      mu: -0.02,
      diffusion: 0.006,
      kappa: 0.35,
      tailEta: 0.55,
      downsideAsym: 0.45,
      horizon: 3.0,
      stableBand: 0.05,
      naiveP0: 0.8,
    },
  },
];

const FALLBACK_OB_PRESETS = [
  {
    id: "calm",
    label: "Pasar Tenang",
    subtitle: "Bid/ask & flow seimbang",
    params: {
      bidDepth: 120,
      askDepth: 120,
      takerBuyVol: 45,
      takerSellVol: 45,
      canceledBidVol: 10,
      canceledAskVol: 10,
      sigmaPerSec: 0.0005,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
  {
    id: "spoof_sell",
    label: "Spoofing Jual",
    subtitle: "Tembok ask palsu + cancel tinggi",
    params: {
      bidDepth: 45,
      askDepth: 260,
      takerBuyVol: 38,
      takerSellVol: 42,
      canceledBidVol: 8,
      canceledAskVol: 340,
      sigmaPerSec: 0.0007,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
  {
    id: "spoof_buy",
    label: "Spoofing Beli",
    subtitle: "Tembok bid palsu + cancel tinggi",
    params: {
      bidDepth: 260,
      askDepth: 45,
      takerBuyVol: 41,
      takerSellVol: 39,
      canceledBidVol: 340,
      canceledAskVol: 8,
      sigmaPerSec: 0.0007,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
  {
    id: "real_dump",
    label: "Panic Dump Riil",
    subtitle: "Market sell menghantam bid",
    params: {
      bidDepth: 45,
      askDepth: 220,
      takerBuyVol: 18,
      takerSellVol: 95,
      canceledBidVol: 15,
      canceledAskVol: 25,
      sigmaPerSec: 0.0011,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
  {
    id: "real_pump",
    label: "Breakout Beli Riil",
    subtitle: "Market buy menyapu ask",
    params: {
      bidDepth: 230,
      askDepth: 55,
      takerBuyVol: 95,
      takerSellVol: 22,
      canceledBidVol: 20,
      canceledAskVol: 12,
      sigmaPerSec: 0.0009,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
  {
    id: "whipsaw",
    label: "Whipsaw / Rilis Makro",
    subtitle: "Volatilitas meledak 2 arah",
    params: {
      bidDepth: 100,
      askDepth: 100,
      takerBuyVol: 85,
      takerSellVol: 85,
      canceledBidVol: 45,
      canceledAskVol: 45,
      sigmaPerSec: 0.002,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
];

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(digits);
}

function fmtSignedNum(v, digits = 2, suffix = "") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}${suffix}`;
}

/**
 * Horizontal stacked probability distribution bar: [Downside | Stable | Upside].
 * Solid research palette — zero gradients.
 */
function TriZoneBar({ pDown, pStable, pUp }) {
  const d = Math.max(0, Math.min(100, (Number(pDown) || 0) * 100));
  const s = Math.max(0, Math.min(100, (Number(pStable) || 0) * 100));
  const u = Math.max(0, Math.min(100, (Number(pUp) || 0) * 100));
  return (
    <div className="min-w-[168px]">
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-line">
        <div style={{ width: `${d}%`, backgroundColor: "#B3402F" }} title={`Downside ${d.toFixed(1)}%`} />
        <div style={{ width: `${s}%`, backgroundColor: "#144338" }} title={`Stabil ${s.toFixed(1)}%`} />
        <div style={{ width: `${u}%`, backgroundColor: "#B18A44" }} title={`Upside ${u.toFixed(1)}%`} />
      </div>
      <div className="mt-1 flex justify-between font-mono text-[10px] tabular-nums text-muted">
        <span className="text-down">{d.toFixed(0)}%</span>
        <span className="font-semibold text-pine-700">{s.toFixed(1)}%</span>
        <span className="text-gold-600">{u.toFixed(0)}%</span>
      </div>
    </div>
  );
}

/**
 * 5-Zone Fokker–Planck Probability Density Chart P(x, T).
 */
function PdfCurveChart({ curve, stableBand, refPrice, unitMode = "pct", height = 310 }) {
  if (!Array.isArray(curve) || curve.length === 0) {
    return (
      <div className="grid place-items-center h-64 text-sm text-muted">
        Kurva distribusi belum tersedia.
      </div>
    );
  }
  const bandPct = Number((stableBand * 100).toFixed(4));
  const twoBandPct = Number((stableBand * 200).toFixed(4));
  const digits = unitMode === "micro" ? 2 : 1;

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={curve} margin={{ top: 12, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid stroke="#E8E5DE" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="xPct"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v) => `${v > 0 ? "+" : ""}${Number(v).toFixed(digits)}%`}
            tick={{ fill: "#68716A", fontSize: 11, fontFamily: "IBM Plex Mono, monospace" }}
            stroke="#D9D5CB"
          />
          <YAxis
            tickFormatter={(v) => Number(v).toFixed(2)}
            tick={{ fill: "#9AA096", fontSize: 10, fontFamily: "IBM Plex Mono, monospace" }}
            stroke="#D9D5CB"
            width={46}
          />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload || !payload.length) return null;
              const pt = payload[0]?.payload;
              if (!pt) return null;
              const xPct = Number(pt.xPct);
              const absX = Math.abs(pt.x);
              let zoneName = "Zona Stabil";
              let zoneColor = "#144338";
              if (pt.x < -2 * stableBand) {
                zoneName = "Ekor Crash (<-2a)";
                zoneColor = "#8A3324";
              } else if (pt.x < -stableBand) {
                zoneName = "Zona Downside";
                zoneColor = "#B3402F";
              } else if (pt.x > 2 * stableBand) {
                zoneName = "Ekor Breakout (>+2a)";
                zoneColor = "#1F6F4A";
              } else if (absX > stableBand) {
                zoneName = "Zona Upside";
                zoneColor = "#8F6E33";
              }
              const impliedPrice =
                refPrice && refPrice > 0 ? refPrice * Math.exp(Number(pt.x)) : null;
              return (
                <div className="rounded-xl border border-lineStrong bg-card px-3.5 py-2.5 shadow-card text-xs">
                  <p className="font-mono text-[10px] uppercase tracking-micro" style={{ color: zoneColor }}>
                    {zoneName}
                  </p>
                  <p className="mt-1 font-mono text-[12px] font-semibold text-ink tabular-nums">
                    Log-return x: {xPct > 0 ? "+" : ""}
                    {xPct.toFixed(unitMode === "micro" ? 2 : 2)}%
                  </p>
                  {impliedPrice !== null && (
                    <p className="font-mono text-[11px] text-muted tabular-nums">
                      Estimasi harga: ${fmtPrice(impliedPrice)}
                    </p>
                  )}
                  <p className="font-mono text-[11px] text-muted tabular-nums">
                    Kepadatan P(x,T): {Number(pt.density).toFixed(3)}
                  </p>
                </div>
              );
            }}
          />
          <ReferenceLine x={0} stroke="#1F2721" strokeDasharray="3 3" strokeOpacity={0.55} />
          <ReferenceLine x={-bandPct} stroke="#B3402F" strokeDasharray="4 4" strokeOpacity={0.7} />
          <ReferenceLine x={bandPct} stroke="#144338" strokeDasharray="4 4" strokeOpacity={0.7} />
          <ReferenceLine x={-twoBandPct} stroke="#8A3324" strokeDasharray="2 4" strokeOpacity={0.45} />
          <ReferenceLine x={twoBandPct} stroke="#1F6F4A" strokeDasharray="2 4" strokeOpacity={0.45} />

          <Area
            type="monotone"
            dataKey="crash"
            stroke="#8A3324"
            strokeWidth={1.75}
            fill="#E3B7AC"
            fillOpacity={0.65}
            isAnimationActive={false}
            connectNulls={false}
          />
          <Area
            type="monotone"
            dataKey="downside"
            stroke="#B3402F"
            strokeWidth={1.75}
            fill="#F6E4E0"
            fillOpacity={0.85}
            isAnimationActive={false}
            connectNulls={false}
          />
          <Area
            type="monotone"
            dataKey="stable"
            stroke="#144338"
            strokeWidth={2.2}
            fill="#E3EBE6"
            fillOpacity={0.9}
            isAnimationActive={false}
            connectNulls={false}
          />
          <Area
            type="monotone"
            dataKey="upside"
            stroke="#B18A44"
            strokeWidth={1.75}
            fill="#F3ECDE"
            fillOpacity={0.9}
            isAnimationActive={false}
            connectNulls={false}
          />
          <Area
            type="monotone"
            dataKey="pump"
            stroke="#1F6F4A"
            strokeWidth={1.75}
            fill="#C3D4C9"
            fillOpacity={0.7}
            isAnimationActive={false}
            connectNulls={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Time-evolution chart for P_stable(t), P_downside(t), P_upside(t).
 */
function TimelineChart({ timeline, unitLabel = "h", height = 240 }) {
  const data = useMemo(
    () =>
      (timeline || []).map((row) => ({
        tLabel: `${Number(row.t).toFixed(row.t < 5 ? 1 : 0)}${unitLabel}`,
        pStable: Number((row.pStable * 100).toFixed(1)),
        pDownside: Number((row.pDownside * 100).toFixed(1)),
        pUpside: Number((row.pUpside * 100).toFixed(1)),
        meanPct: Number((row.meanLogReturn * 100).toFixed(3)),
      })),
    [timeline, unitLabel]
  );

  if (data.length === 0) return null;

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, left: -6, bottom: 4 }}>
          <CartesianGrid stroke="#E8E5DE" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="tLabel"
            tick={{ fill: "#68716A", fontSize: 11, fontFamily: "IBM Plex Mono, monospace" }}
            stroke="#D9D5CB"
          />
          <YAxis
            domain={[0, 100]}
            tickFormatter={(v) => `${v}%`}
            tick={{ fill: "#9AA096", fontSize: 10, fontFamily: "IBM Plex Mono, monospace" }}
            stroke="#D9D5CB"
            width={44}
          />
          <Tooltip
            formatter={(val, name) => [`${val}%`, name]}
            contentStyle={{
              backgroundColor: "#FFFFFF",
              borderColor: "#D9D5CB",
              borderRadius: "12px",
              fontSize: "12px",
              fontFamily: "IBM Plex Mono, monospace",
            }}
          />
          <Legend wrapperStyle={{ fontSize: "11px", fontFamily: "IBM Plex Mono, monospace" }} />
          <Line
            type="monotone"
            dataKey="pStable"
            name="P(Stabil)"
            stroke="#144338"
            strokeWidth={2.2}
            dot={{ r: 3, fill: "#144338" }}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="pDownside"
            name="P(Down/Dump)"
            stroke="#B3402F"
            strokeWidth={2}
            dot={{ r: 3, fill: "#B3402F" }}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="pUpside"
            name="P(Up/Pump)"
            stroke="#B18A44"
            strokeWidth={2}
            dot={{ r: 3, fill: "#B18A44" }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function SliderField({ label, value, min, max, step, onChange, display }) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <span className="text-[12px] font-medium text-ink">{label}</span>
        <span className="font-mono text-[11.5px] font-semibold text-pine-700 tabular-nums">
          {display !== undefined ? display : value}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-pine-700 cursor-pointer"
      />
    </label>
  );
}

export default function FokkerPlanckPage() {
  const { symbol: routeSymbol } = useParams();
  const navigate = useNavigate();

  const [activeTab, setActiveTab] = useState(routeSymbol ? "asset" : "universe");
  const [spec, setSpec] = useState(null);

  // Shared candle PDE parameters
  const [horizonDays, setHorizonDays] = useState(7);
  const [stableBand, setStableBand] = useState(0.05);

  // Tab 1: Universe state
  const [universeData, setUniverseData] = useState(null);
  const [uniLoading, setUniLoading] = useState(true);
  const [uniError, setUniError] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [regimeFilter, setRegimeFilter] = useState("ALL");
  const [sortBy, setSortBy] = useState({ key: "pStable", dir: "desc" });

  // Tab 2: Single-asset deep dive
  const [selectedSymbol, setSelectedSymbol] = useState(
    routeSymbol ? routeSymbol.toUpperCase() : "BTCUSDT"
  );
  const [customSymInput, setCustomSymInput] = useState("");
  const [assetData, setAssetData] = useState(null);
  const [assetLoading, setAssetLoading] = useState(false);
  const [assetError, setAssetError] = useState(null);

  // Tab 3: Microstructure spoofing & direct PDE lab
  const [simMode, setSimMode] = useState("orderbook"); // "orderbook" | "direct"
  const [activePresetId, setActivePresetId] = useState("spoof_sell");
  const [obParams, setObParams] = useState(FALLBACK_OB_PRESETS[1].params);
  const [directParams, setDirectParams] = useState(DIRECT_PRESETS[0].values);
  const [simResult, setSimResult] = useState(null);
  const [simBusy, setSimBusy] = useState(false);
  const [simError, setSimError] = useState(null);
  const [snapBusy, setSnapBusy] = useState(false);
  const [snapMeta, setSnapMeta] = useState(null);

  // Optional browser-direct Binance order-book WebSocket
  const [wsEnabled, setWsEnabled] = useState(false);
  const [wsStatus, setWsStatus] = useState("idle"); // "idle" | "connecting" | "live" | "offline"
  const [wsMessage, setWsMessage] = useState(null);
  const wsUnsubRef = useRef(null);

  // Tab 4: Walk-forward calibration audit
  const [btParams, setBtParams] = useState({
    horizonDays: 7,
    stableBand: 0.05,
    stepDays: 7,
    lookbackDays: 240,
    limit: 16,
  });
  const [btResult, setBtResult] = useState(null);
  const [btBusy, setBtBusy] = useState(false);
  const [btError, setBtError] = useState(null);

  // Sync URL param if navigated directly to /fokker-planck/:symbol
  useEffect(() => {
    if (routeSymbol) {
      const clean = routeSymbol.toUpperCase();
      setSelectedSymbol(clean.endsWith("USDT") ? clean : `${clean}USDT`);
      setActiveTab("asset");
    }
  }, [routeSymbol]);

  // Load formula specification & presets once
  useEffect(() => {
    let alive = true;
    fetchFpSpec()
      .then((res) => {
        if (alive) setSpec(res);
      })
      .catch(() => {
        /* fallback presets are already bundled */
      });
    return () => {
      alive = false;
    };
  }, []);

  // Load Universe table whenever horizonDays or stableBand changes
  const loadUniverse = useCallback(async () => {
    setUniLoading(true);
    setUniError(null);
    try {
      const res = await fetchFpUniverse({
        limit: 18,
        horizonDays,
        stableBand,
      });
      setUniverseData(res);
    } catch (err) {
      setUniError(err?.message || "Gagal memuat data universe Fokker–Planck.");
    } finally {
      setUniLoading(false);
    }
  }, [horizonDays, stableBand]);

  useEffect(() => {
    loadUniverse();
  }, [loadUniverse]);

  // Live ticker prices for universe symbols (browser-direct Binance miniTicker, safe offline)
  const universeSymbols = useMemo(
    () => (universeData?.data || []).map((row) => row.symbol),
    [universeData]
  );
  const { ticks: liveTicks, live: wsTickersLive } = useLiveTickers(universeSymbols);

  // Load single-asset deep dive when activeTab === "asset" or parameters change
  const loadAsset = useCallback(
    async (sym) => {
      const target = String(sym || "BTCUSDT").toUpperCase();
      setAssetLoading(true);
      setAssetError(null);
      try {
        const res = await fetchFpAsset(target, {
          horizonDays,
          stableBand,
          microHorizonSec: 30,
          microBand: 0.004,
        });
        setAssetData(res);
      } catch (err) {
        setAssetError(err?.message || `Gagal menganalisis ${target}.`);
      } finally {
        setAssetLoading(false);
      }
    },
    [horizonDays, stableBand]
  );

  useEffect(() => {
    if (activeTab === "asset") {
      loadAsset(selectedSymbol);
    }
  }, [activeTab, selectedSymbol, loadAsset]);

  // Debounced PDE simulator in Tab 3
  useEffect(() => {
    if (activeTab !== "spoofing") return undefined;
    let alive = true;
    const timer = setTimeout(async () => {
      setSimBusy(true);
      setSimError(null);
      try {
        const payload =
          simMode === "direct"
            ? { mode: "direct", ...directParams }
            : { mode: "orderbook", ...obParams };
        const res = await simulateFp(payload);
        if (alive) setSimResult(res);
      } catch (err) {
        if (alive) setSimError(err?.message || "Simulasi PDE gagal dijalankan.");
      } finally {
        if (alive) setSimBusy(false);
      }
    }, 140);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [activeTab, simMode, obParams, directParams]);

  // Optional browser-direct WebSocket subscription for live order book + trades
  useEffect(() => {
    if (!wsEnabled || activeTab !== "spoofing" || simMode !== "orderbook") {
      if (wsUnsubRef.current) {
        wsUnsubRef.current();
        wsUnsubRef.current = null;
      }
      if (!wsEnabled) setWsStatus("idle");
      return undefined;
    }

    setWsMessage(null);
    const unsub = subscribeOrderbookStream(
      selectedSymbol,
      (snap) => {
        setActivePresetId("live_ws");
        setSnapMeta({
          live: true,
          source: "binance-ws-live",
          symbol: snap.symbol,
          midPrice: snap.midPrice,
          spreadBps: snap.spreadBps,
          note: `Stream live @depth20 + @aggTrade (${snap.samples} trade dalam 15 dtk terakhir).`,
        });
        setObParams((prev) => ({
          ...prev,
          bidDepth: Number(snap.bidDepth.toFixed(4)),
          askDepth: Number(snap.askDepth.toFixed(4)),
          takerBuyVol: Number(snap.takerBuyVol.toFixed(4)),
          takerSellVol: Number(snap.takerSellVol.toFixed(4)),
          canceledBidVol: Number(snap.canceledBidVol.toFixed(4)),
          canceledAskVol: Number(snap.canceledAskVol.toFixed(4)),
          sigmaPerSec: Number(snap.sigmaPerSec.toFixed(6)),
          midPrice: snap.midPrice,
        }));
      },
      (status, reason) => {
        setWsStatus(status);
        if (status === "offline") {
          setWsEnabled(false);
          if (reason) setWsMessage(reason);
        }
      }
    );
    wsUnsubRef.current = unsub;
    return () => {
      unsub();
      wsUnsubRef.current = null;
    };
  }, [wsEnabled, activeTab, simMode, selectedSymbol]);

  // Pull order-book + trades snapshot from existing API chain into Tab 3
  const handlePullApiSnapshot = async (symOverride) => {
    const sym = String(symOverride || selectedSymbol || "BTCUSDT").toUpperCase();
    setWsEnabled(false);
    setSimMode("orderbook");
    setSnapBusy(true);
    setSimError(null);
    try {
      const res = await fetchFpAsset(sym, {
        horizonDays,
        stableBand,
        microHorizonSec: obParams.horizonSec || 30,
        microBand: obParams.stableBand || 0.004,
      });
      const micro = res?.microstructure;
      const inp = micro?.inputs;
      if (inp) {
        setActivePresetId("api_snapshot");
        setObParams((prev) => ({
          ...prev,
          bidDepth: inp.bidDepth,
          askDepth: inp.askDepth,
          takerBuyVol: inp.takerBuyVol,
          takerSellVol: inp.takerSellVol,
          canceledBidVol: inp.canceledBidVol,
          canceledAskVol: inp.canceledAskVol,
          sigmaPerSec: inp.sigmaPerSec,
          midPrice: micro?.analysis?.midPrice || res?.candleAnalysis?.price,
        }));
        setSnapMeta({
          live: Boolean(micro.live),
          source: micro.source,
          symbol: res.symbol,
          midPrice: micro?.analysis?.midPrice || res?.candleAnalysis?.price,
          spreadBps: inp.spreadBps,
          cancelDiffAvailable: inp.cancelDiffAvailable,
          note: micro.warning || null,
        });
      }
    } catch (err) {
      setSimError(err?.message || `Gagal menarik snapshot ${sym}.`);
    } finally {
      setSnapBusy(false);
    }
  };

  // Run chronological walk-forward calibration audit in Tab 4
  const handleRunBacktest = async () => {
    setBtBusy(true);
    setBtError(null);
    try {
      const res = await runFpBacktest(btParams);
      setBtResult(res);
    } catch (err) {
      setBtError(err?.message || "Gagal menjalankan uji walk-forward.");
    } finally {
      setBtBusy(false);
    }
  };

  const openAssetDetail = (sym) => {
    const clean = String(sym || "").toUpperCase();
    setSelectedSymbol(clean);
    setActiveTab("asset");
    navigate(`/fokker-planck/${clean}`, { replace: false });
  };

  const handleCustomSymbolSubmit = (e) => {
    e.preventDefault();
    const raw = customSymInput.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!raw) return;
    const full = raw.endsWith("USDT") ? raw : `${raw}USDT`;
    setCustomSymInput("");
    openAssetDetail(full);
  };

  const handleToggleSort = (key) => {
    setSortBy((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "desc" ? "asc" : "desc" }
        : { key, dir: "desc" }
    );
  };

  // Filtered + sorted universe table rows
  const filteredRows = useMemo(() => {
    const rows = universeData?.data || [];
    const q = searchQuery.trim().toUpperCase();
    const list = rows.filter((r) => {
      if (q && !r.symbol.includes(q) && !r.baseAsset.includes(q)) return false;
      if (regimeFilter === "BULL") {
        return r.regime === "ACCUMULATION_STABLE" || r.regime === "BULLISH_EXPANSION";
      }
      if (regimeFilter === "CALM") return r.regime === "SIDEWAYS_CALM";
      if (regimeFilter === "VOL") return r.regime === "HIGH_VOL_DISPERSED";
      if (regimeFilter === "RISK") {
        return r.regime === "DOWNSIDE_SKEW" || r.regime === "TAIL_CRASH_RISK";
      }
      return true;
    });

    const getVal = (row) => {
      switch (sortBy.key) {
        case "pStable":
          return row.metrics.pStable;
        case "pDownside":
          return row.metrics.pDownside;
        case "pUpside":
          return row.metrics.pUpside;
        case "pTailCrash":
          return row.metrics.pTailCrash;
        case "expectedReturnPct":
          return row.metrics.expectedReturnPct;
        case "sigmaDailyPct":
          return row.parameters.sigmaDailyPct;
        case "kurtosis":
          return row.metrics.kurtosis;
        case "price":
          return liveTicks[row.symbol]?.price ?? row.price ?? 0;
        default:
          return row.metrics.pStable;
      }
    };

    return [...list].sort((a, b) => {
      const diff = getVal(a) - getVal(b);
      return sortBy.dir === "desc" ? -diff : diff;
    });
  }, [universeData, searchQuery, regimeFilter, sortBy, liveTicks]);

  const obPresets = spec?.presets?.length ? spec.presets : FALLBACK_OB_PRESETS;
  const candleRes = assetData?.candleAnalysis;
  const { obMaxScale, obStep, obDigits } = useMemo(() => {
    const rawPeak = Math.max(
      obParams.bidDepth || 0,
      obParams.askDepth || 0,
      obParams.takerBuyVol || 0,
      obParams.takerSellVol || 0,
      obParams.canceledBidVol || 0,
      obParams.canceledAskVol || 0,
      1
    );
    const maxVal = Math.max(0.1, Number((rawPeak * 1.5).toPrecision(2)));
    const stepVal = Math.max(0.001, Number((maxVal / 200).toPrecision(2)));
    const digits = maxVal < 20 ? 2 : maxVal < 2000 ? 1 : 0;
    return { obMaxScale: maxVal, obStep: stepVal, obDigits: digits };
  }, [
    obParams.bidDepth,
    obParams.askDepth,
    obParams.takerBuyVol,
    obParams.takerSellVol,
    obParams.canceledBidVol,
    obParams.canceledAskVol,
  ]);

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-8 md:py-10">
      {/* ---------- Editorial page header ---------- */}
      <div className="border-b border-line pb-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="micro-label text-gold-600">
            Kolmogorov Forward PDE · Distribusi Probabilitas & Mikrostruktur
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {universeData && (
              <span
                className={`chip ${
                  universeData.live
                    ? "border-pine-200 bg-pine-50 text-pine-700"
                    : "border-gold-300 bg-gold-100 text-gold-700"
                }`}
              >
                <span
                  className={`inline-block w-1.5 h-1.5 rounded-full ${
                    universeData.live ? "bg-up" : "bg-gold-600"
                  }`}
                />
                {universeData.live
                  ? `API Live · ${universeData.source}`
                  : `Demo Seed · ${universeData.source}`}
              </span>
            )}
            {wsTickersLive && (
              <span className="chip-live">
                <span className="live-dot" /> Harga WS Live
              </span>
            )}
            <span className="chip border-lineStrong bg-card text-muted">
              ∫P(x,t)dx = 1.000
            </span>
          </div>
        </div>

        <h1 className="font-display text-3xl sm:text-4xl font-semibold tracking-tight text-ink mt-2">
          Analisis Fokker–Planck Crypto
        </h1>
        <p className="mt-2.5 text-[14.5px] leading-6 text-muted max-w-3xl">
          Mengevolusikan kurva kepadatan probabilitas log-return{" "}
          <code className="font-mono text-[12px] bg-pine-50 text-pine-800 px-1.5 py-0.5 rounded">
            P(x,t)
          </code>{" "}
          di bawah tekanan aliran (<em>drift</em> μ) dan volatilitas berekor tebal (
          <em>difusi</em> D) menggunakan skema konservatif Chang–Cooper — terisolasi penuh
          tanpa mengubah skor produksi Screener utama.
        </p>

        {/* ---------- Demo-data honest notice ---------- */}
        {universeData && !universeData.live && (
          <div className="mt-4 flex items-start gap-2.5 rounded-xl border border-gold-300 bg-gold-100/70 px-4 py-3 text-[12.5px] text-gold-700">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            <span>
              <strong>Mode Data Demo (Seed Fallback):</strong> Endpoint bursa publik tidak
              terjangkau dari jaringan server saat ini — menggunakan candle deterministik
              bawaan Meridian agar seluruh analisis, grafik PDF, simulator spoofing, dan
              uji walk-forward tetap berjalan penuh. Saat terhubung ke Binance/MEXC/CoinGecko,
              data otomatis beralih ke live.
            </span>
          </div>
        )}

        {/* ---------- Sub-navigation tabs ---------- */}
        <div className="mt-6 flex flex-wrap items-center gap-2" role="tablist">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = activeTab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => {
                  setActiveTab(t.id);
                  if (t.id !== "asset" && routeSymbol) {
                    navigate("/fokker-planck", { replace: true });
                  }
                }}
                className={`focus-ring inline-flex items-center gap-2 rounded-full px-4 py-2 text-[13px] font-medium transition-colors border ${
                  active
                    ? "bg-pine-800 text-paper border-pine-800"
                    : "bg-card text-muted border-lineStrong hover:text-ink hover:border-pine-400"
                }`}
              >
                <Icon size={14} strokeWidth={1.9} />
                {t.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* ================================================================== */}
      {/* TAB 1 — PETA UNIVERSE (OHLCV CANDLE SCREENER)                      */}
      {/* ================================================================== */}
      {activeTab === "universe" && (
        <section className="mt-7 space-y-6 animate-fade-up">
          {/* Controls bar */}
          <div className="card p-4 sm:p-5 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
            <div className="flex flex-wrap items-center gap-4">
              <div>
                <span className="micro-label block mb-1.5">Horizon Evolusi (t)</span>
                <div className="inline-flex flex-wrap rounded-full border border-lineStrong bg-paper p-0.5">
                  {HORIZON_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setHorizonDays(opt.value)}
                      className={`rounded-full px-3 py-1 font-mono text-[11px] transition-colors ${
                        horizonDays === opt.value
                          ? "bg-pine-700 text-paper font-semibold"
                          : "text-muted hover:text-ink"
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <span className="micro-label block mb-1.5">Zona Stabil (±a)</span>
                <div className="inline-flex flex-wrap rounded-full border border-lineStrong bg-paper p-0.5">
                  {BAND_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setStableBand(opt.value)}
                      className={`rounded-full px-3 py-1 font-mono text-[11px] transition-colors ${
                        stableBand === opt.value
                          ? "bg-pine-700 text-paper font-semibold"
                          : "text-muted hover:text-ink"
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2.5">
              <div className="relative">
                <Search
                  size={14}
                  className="absolute left-3.5 top-1/2 -translate-y-1/2 text-faint pointer-events-none"
                />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Cari koin (mis. BTC, SOL)…"
                  className="focus-ring rounded-full border border-lineStrong bg-paper pl-9 pr-4 py-2 text-[12.5px] text-ink placeholder:text-faint w-52"
                />
              </div>
              <button
                type="button"
                onClick={loadUniverse}
                disabled={uniLoading}
                className="btn-ghost !py-2 !px-3.5 text-[12.5px]"
              >
                <RefreshCw size={14} className={uniLoading ? "animate-spin" : ""} />
                Muat Ulang
              </button>
            </div>
          </div>

          {/* Summary StatTiles */}
          {universeData?.summary && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <StatTile
                label={`Rata-rata P(Stabil ±${(stableBand * 100).toFixed(0)}%)`}
                value={fmtPct(universeData.summary.avgPStable, { digits: 1 })}
                sub={`Horizon ${horizonDays} hari · ${universeData.summary.universe} aset USDT`}
                tone="pine"
              />
              <StatTile
                label="Akumulasi & Ekspansi Naik"
                value={universeData.summary.stableAccumCount}
                sub={`Rata-rata P(Upside) ${fmtPct(universeData.summary.avgPUpside, { digits: 1 })}`}
                tone="pine"
              />
              <StatTile
                label="Sideways / Dispersi"
                value={`${universeData.summary.calmCount} / ${universeData.summary.dispersedCount}`}
                sub="Tenang vs volatilitas menyebar 2 arah"
                tone="gold"
              />
              <StatTile
                label="Condong Turun & Crash Risk"
                value={universeData.summary.tailRiskCount}
                sub={`Rata-rata P(Downside) ${fmtPct(universeData.summary.avgPDownside, { digits: 1 })}`}
                tone="red"
              />
            </div>
          )}

          {/* Regime filter pills */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-1.5">
              {REGIME_FILTERS.map((rf) => (
                <button
                  key={rf.id}
                  type="button"
                  onClick={() => setRegimeFilter(rf.id)}
                  className={`rounded-full px-3 py-1.5 text-[12px] font-medium border transition-colors ${
                    regimeFilter === rf.id
                      ? "bg-pine-700 text-paper border-pine-700"
                      : "bg-card text-muted border-line hover:border-lineStrong hover:text-ink"
                  }`}
                >
                  {rf.label}
                </button>
              ))}
            </div>
            <p className="font-mono text-[10px] uppercase tracking-micro text-muted">
              Klik baris aset untuk membuka kurva PDF P(x,T) & snapshot spoofing
            </p>
          </div>

          {/* Error state */}
          {uniError && (
            <div className="card p-5 border-[#E3B7AC] bg-[#F6E4E0] text-[#8A3324] text-sm">
              {uniError}
            </div>
          )}

          {/* Universe table */}
          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Aset</th>
                    <th className="sortable" onClick={() => handleToggleSort("price")}>
                      <span className="inline-flex items-center gap-1">
                        Harga <ArrowUpDown size={11} />
                      </span>
                    </th>
                    <th className="sortable" onClick={() => handleToggleSort("pStable")}>
                      <span className="inline-flex items-center gap-1">
                        Down · Stabil · Up <ArrowUpDown size={11} />
                      </span>
                    </th>
                    <th className="sortable" onClick={() => handleToggleSort("pTailCrash")}>
                      <span className="inline-flex items-center gap-1">
                        Ekor Crash (&lt;-{(stableBand * 200).toFixed(0)}%) <ArrowUpDown size={11} />
                      </span>
                    </th>
                    <th className="sortable" onClick={() => handleToggleSort("expectedReturnPct")}>
                      <span className="inline-flex items-center gap-1">
                        E[r {horizonDays}d] <ArrowUpDown size={11} />
                      </span>
                    </th>
                    <th className="sortable" onClick={() => handleToggleSort("sigmaDailyPct")}>
                      <span className="inline-flex items-center gap-1">
                        σ / μ Harian <ArrowUpDown size={11} />
                      </span>
                    </th>
                    <th className="sortable" onClick={() => handleToggleSort("kurtosis")}>
                      <span className="inline-flex items-center gap-1">
                        Kurtosis <ArrowUpDown size={11} />
                      </span>
                    </th>
                    <th>Rezim Fokker–Planck</th>
                    <th>Screener v1</th>
                  </tr>
                </thead>
                <tbody>
                  {uniLoading && !universeData
                    ? Array.from({ length: 7 }).map((_, idx) => (
                        <tr key={idx}>
                          <td colSpan={9}>
                            <div className="skeleton h-7 w-full" />
                          </td>
                        </tr>
                      ))
                    : filteredRows.map((row) => {
                        const tk = liveTicks[row.symbol];
                        const price = tk?.price ?? row.price;
                        const chg24 = tk?.changePercent ?? row.priceChange24h;
                        const expR = row.metrics.expectedReturnPct;
                        return (
                          <tr
                            key={row.symbol}
                            className="row-link"
                            onClick={() => openAssetDetail(row.symbol)}
                          >
                            <td>
                              <div className="flex items-center gap-2.5">
                                <span className="font-mono text-[13px] font-semibold text-ink">
                                  {row.baseAsset}
                                </span>
                                <span className="font-mono text-[10px] text-faint">/USDT</span>
                                {row.cryptorank?.rank && (
                                  <span className="font-mono text-[9.5px] px-1.5 py-0.5 rounded bg-pine-50 text-muted">
                                    #{row.cryptorank.rank}
                                  </span>
                                )}
                              </div>
                              {row.quoteVolume24h ? (
                                <span className="block font-mono text-[10px] text-faint tabular-nums">
                                  Vol 24h {fmtUsdCompact(row.quoteVolume24h)}
                                </span>
                              ) : null}
                            </td>
                            <td className="font-mono text-[12.5px] tabular-nums">
                              <div className="text-ink font-medium">${fmtPrice(price)}</div>
                              {chg24 !== null && chg24 !== undefined && (
                                <div
                                  className={`text-[10.5px] ${
                                    chg24 >= 0 ? "text-up" : "text-down"
                                  }`}
                                >
                                  {fmtSignedNum(chg24, 2, "%")}
                                </div>
                              )}
                            </td>
                            <td>
                              <TriZoneBar
                                pDown={row.metrics.pDownside}
                                pStable={row.metrics.pStable}
                                pUp={row.metrics.pUpside}
                              />
                            </td>
                            <td className="font-mono text-[12px] tabular-nums">
                              <span
                                className={
                                  row.metrics.pTailCrash >= 0.2
                                    ? "font-semibold text-down"
                                    : row.metrics.pTailCrash >= 0.08
                                    ? "text-gold-600"
                                    : "text-muted"
                                }
                              >
                                {fmtPct(row.metrics.pTailCrash, { digits: 1 })}
                              </span>
                            </td>
                            <td
                              className={`font-mono text-[12px] font-medium tabular-nums ${
                                expR > 0.2
                                  ? "text-up"
                                  : expR < -0.2
                                  ? "text-down"
                                  : "text-muted"
                              }`}
                            >
                              {fmtSignedNum(expR, 2, "%")}
                            </td>
                            <td className="font-mono text-[11.5px] tabular-nums text-muted">
                              <div>σ {fmtNum(row.parameters.sigmaDailyPct, 2)}%</div>
                              <div className="text-[10px] text-faint">
                                μ {fmtSignedNum(row.parameters.muFlowDailyPct, 2, "%")}
                              </div>
                            </td>
                            <td className="font-mono text-[12px] tabular-nums text-muted">
                              {fmtNum(row.metrics.kurtosis, 2)}
                            </td>
                            <td>
                              <VerdictBadge tone={row.regimeTone} label={row.regimeLabel} />
                            </td>
                            <td>
                              <VerdictBadge
                                tone={row.screening.verdictTone}
                                label={row.screening.verdictLabel}
                              />
                            </td>
                          </tr>
                        );
                      })}
                  {!uniLoading && filteredRows.length === 0 && (
                    <tr>
                      <td colSpan={9} className="text-center py-10 text-muted text-sm">
                        Tidak ada aset yang cocok dengan filter saat ini.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}

      {/* ================================================================== */}
      {/* TAB 2 — KURVA DISTRIBUSI ASET (SINGLE-ASSET DEEP DIVE)             */}
      {/* ================================================================== */}
      {activeTab === "asset" && (
        <section className="mt-7 space-y-6 animate-fade-up">
          {/* Symbol selector & horizon controls */}
          <div className="card p-4 sm:p-5 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
            <div className="flex flex-wrap items-center gap-3">
              <div>
                <span className="micro-label block mb-1">Pilih Aset Universe</span>
                <select
                  value={selectedSymbol}
                  onChange={(e) => openAssetDetail(e.target.value)}
                  className="focus-ring rounded-full border border-lineStrong bg-paper px-4 py-2 font-mono text-[12.5px] font-semibold text-ink"
                >
                  {Array.from(
                    new Set([
                      selectedSymbol,
                      ...(universeData?.data || []).map((r) => r.symbol),
                    ])
                  ).map((sym) => (
                    <option key={sym} value={sym}>
                      {sym}
                    </option>
                  ))}
                </select>
              </div>

              <form onSubmit={handleCustomSymbolSubmit} className="flex items-end gap-2">
                <div>
                  <span className="micro-label block mb-1">Simbol Lain</span>
                  <input
                    type="text"
                    value={customSymInput}
                    onChange={(e) => setCustomSymInput(e.target.value)}
                    placeholder="mis. PEPE, SUI"
                    className="focus-ring rounded-full border border-lineStrong bg-paper px-3.5 py-2 font-mono text-[12px] text-ink placeholder:text-faint w-36"
                  />
                </div>
                <button type="submit" className="btn-ghost !py-2 !px-3.5 text-[12px]">
                  Uji
                </button>
              </form>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <div>
                <span className="micro-label block mb-1">Horizon</span>
                <select
                  value={horizonDays}
                  onChange={(e) => setHorizonDays(Number(e.target.value))}
                  className="focus-ring rounded-full border border-lineStrong bg-paper px-3.5 py-2 font-mono text-[12px] text-ink"
                >
                  {HORIZON_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <span className="micro-label block mb-1">Zona Stabil</span>
                <select
                  value={stableBand}
                  onChange={(e) => setStableBand(Number(e.target.value))}
                  className="focus-ring rounded-full border border-lineStrong bg-paper px-3.5 py-2 font-mono text-[12px] text-ink"
                >
                  {BAND_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="button"
                onClick={() => {
                  setActiveTab("spoofing");
                  handlePullApiSnapshot(selectedSymbol);
                }}
                className="btn-primary !py-2 !px-4 text-[12.5px] mt-4 sm:mt-0"
              >
                Cek Spoofing {selectedSymbol.replace("USDT", "")} <ArrowRight size={14} />
              </button>
            </div>
          </div>

          {assetError && (
            <div className="card p-5 border-[#E3B7AC] bg-[#F6E4E0] text-[#8A3324] text-sm">
              {assetError}
            </div>
          )}

          {assetLoading && !assetData && (
            <div className="card p-8 space-y-4">
              <div className="skeleton h-8 w-64" />
              <div className="skeleton h-64 w-full" />
            </div>
          )}

          {assetData && candleRes && (
            <>
              {/* Asset Banner */}
              <div className="card p-5 sm:p-6 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                <div>
                  <div className="flex flex-wrap items-center gap-3">
                    <h2 className="font-display text-3xl font-semibold text-ink">
                      {assetData.baseAsset}
                      <span className="text-faint font-normal text-xl">/USDT</span>
                    </h2>
                    <VerdictBadge
                      tone={candleRes.regimeTone}
                      label={candleRes.regimeLabel}
                      size="lg"
                    />
                    {assetData.screening && (
                      <span className="chip border-lineStrong bg-paper text-muted">
                        Screener v1: {assetData.screening.verdictLabel}
                      </span>
                    )}
                  </div>
                  <p className="mt-2 text-[13.5px] text-muted max-w-2xl">
                    {candleRes.regimeSummary}
                  </p>
                </div>

                <div className="text-left md:text-right font-mono tabular-nums">
                  <span className="micro-label block">Harga Referensi S₀</span>
                  <span className="text-2xl font-semibold text-ink">
                    ${fmtPrice(candleRes.price)}
                  </span>
                  <span className="block text-[11px] text-muted mt-0.5">
                    VWAP-20 ${fmtPrice(candleRes.vwap20)} · {candleRes.candlesUsed} candle (
                    {assetData.source})
                  </span>
                </div>
              </div>

              {/* 4 Zone Probability Tiles */}
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatTile
                  label={`P(Stabil ±${(candleRes.stableBand * 100).toFixed(0)}%)`}
                  value={fmtPct(candleRes.metrics.pStable, { digits: 1 })}
                  sub={`$${fmtPrice(candleRes.priceBands.lowerStable)} – $${fmtPrice(
                    candleRes.priceBands.upperStable
                  )}`}
                  tone="pine"
                />
                <StatTile
                  label={`P(Downside <-${(candleRes.stableBand * 100).toFixed(0)}%)`}
                  value={fmtPct(candleRes.metrics.pDownside, { digits: 1 })}
                  sub={`Di bawah $${fmtPrice(candleRes.priceBands.lowerStable)}`}
                  tone="red"
                />
                <StatTile
                  label={`P(Upside >+${(candleRes.stableBand * 100).toFixed(0)}%)`}
                  value={fmtPct(candleRes.metrics.pUpside, { digits: 1 })}
                  sub={`Di atas $${fmtPrice(candleRes.priceBands.upperStable)}`}
                  tone="gold"
                />
                <StatTile
                  label={`Ekor Crash (<-${(candleRes.stableBand * 200).toFixed(0)}%)`}
                  value={fmtPct(candleRes.metrics.pTailCrash, { digits: 1 })}
                  sub={`Di bawah $${fmtPrice(candleRes.priceBands.crashPrice)} · Kurt ${fmtNum(
                    candleRes.metrics.kurtosis,
                    2
                  )}`}
                  tone="red"
                />
              </div>

              {/* Main PDF Curve + Horizon Evolution */}
              <div className="grid lg:grid-cols-3 gap-6">
                <div className="card p-5 sm:p-6 lg:col-span-2">
                  <div className="flex flex-wrap items-baseline justify-between gap-2 mb-3">
                    <div>
                      <p className="micro-label">Kurva Kepadatan Probabilitas P(x, T)</p>
                      <h3 className="font-display text-xl font-semibold text-ink mt-0.5">
                        Distribusi Log-Return {candleRes.horizonDays} Hari ke Depan
                      </h3>
                    </div>
                    <span className="font-mono text-[11px] text-muted tabular-nums">
                      Massa Total = {fmtNum(candleRes.metrics.totalMass, 6)}
                    </span>
                  </div>

                  <PdfCurveChart
                    curve={candleRes.curve}
                    stableBand={candleRes.stableBand}
                    refPrice={candleRes.price}
                    unitMode="pct"
                    height={310}
                  />

                  {/* Zone legend */}
                  <div className="mt-3 pt-3 border-t border-line flex flex-wrap items-center gap-x-5 gap-y-1.5 font-mono text-[10.5px] text-muted">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-sm bg-[#E3B7AC] border border-[#8A3324]" />
                      Crash (&lt;-{(candleRes.stableBand * 200).toFixed(0)}%)
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-sm bg-[#F6E4E0] border border-down" />
                      Downside
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-sm bg-pine-100 border border-pine-700" />
                      Zona Stabil (±{(candleRes.stableBand * 100).toFixed(0)}%)
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-sm bg-gold-100 border border-gold-500" />
                      Upside
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-sm bg-pine-200 border border-up" />
                      Breakout (&gt;+{(candleRes.stableBand * 200).toFixed(0)}%)
                    </span>
                  </div>
                </div>

                <div className="card p-5 sm:p-6 flex flex-col justify-between">
                  <div>
                    <p className="micro-label">Lintasan Probabilitas</p>
                    <h3 className="font-display text-xl font-semibold text-ink mt-0.5 mb-3">
                      Evolusi Waktu t = 0 → {candleRes.horizonDays}h
                    </h3>
                    <TimelineChart timeline={candleRes.timeline} unitLabel="h" height={240} />
                  </div>
                  <div className="mt-4 pt-3 border-t border-line space-y-1.5 font-mono text-[11px] tabular-nums">
                    <div className="flex justify-between">
                      <span className="text-muted">Ekspektasi Return E[r]</span>
                      <span className="font-semibold text-ink">
                        {fmtSignedNum(candleRes.metrics.expectedReturnPct, 2, "%")} ($
                        {fmtPrice(candleRes.priceBands.expectedPrice)})
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted">Stabilitas Adaptif (±1σ_T)</span>
                      <span className="font-semibold text-pine-700">
                        {fmtPct(candleRes.metrics.pAdaptive1Sigma, { digits: 1 })}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted">Skewness / Entropi</span>
                      <span className="text-ink">
                        {fmtSignedNum(candleRes.metrics.skewness, 2)} /{" "}
                        {fmtNum(candleRes.metrics.shannonEntropy, 2)}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Parameter decomposition + Analyst Notes */}
              <div className="grid lg:grid-cols-2 gap-6">
                <div className="card p-5 sm:p-6">
                  <p className="micro-label">Dekomposisi Input PDE</p>
                  <h3 className="font-display text-xl font-semibold text-ink mt-0.5 mb-4">
                    Parameter Drift μ(x,t) & Difusi D(x,t)
                  </h3>
                  <dl className="divide-y divide-line text-[13px]">
                    {[
                      [
                        "Drift Aliran Harian (μ_flow)",
                        fmtSignedNum(candleRes.parameters.muFlowDailyPct, 3, "% / hari"),
                      ],
                      [
                        "Volatilitas Difusi Harian (√D₀)",
                        `${fmtNum(candleRes.parameters.sigmaDailyPct, 2)}% (EWMA ${fmtNum(
                          candleRes.parameters.ewmaVolPct,
                          2
                        )}% · Parkinson ${fmtNum(candleRes.parameters.parkinsonVolPct, 2)}%)`,
                      ],
                      [
                        "Koefisien Ekor Tebal (η) & Kurtosis Sampel",
                        `η = ${fmtNum(candleRes.parameters.tailEta, 2)} · kurt = ${fmtNum(
                          candleRes.parameters.sampleKurtosis,
                          2
                        )}`,
                      ],
                      [
                        "Asimetri Downside / Leverage (λ_down)",
                        fmtNum(candleRes.parameters.downsideAsym, 2),
                      ],
                      [
                        "Taker-Buy Share & Imbalance (30d)",
                        `${fmtPct(candleRes.parameters.buyShare30d, { digits: 1 })} (${fmtSignedNum(
                          candleRes.parameters.buyImbalance30d,
                          3
                        )})`,
                      ],
                      [
                        "Bobot Kredibilitas Partisipasi",
                        `${fmtNum(candleRes.parameters.participationWeight, 2)}x (Δtrades ${fmtPct(
                          candleRes.parameters.participationGrowth30d,
                          { signed: true, digits: 1 }
                        )})`,
                      ],
                      [
                        "Jangkar Mean-Reversion VWAP-20",
                        `${fmtSignedNum(candleRes.parameters.vwapAnchorPct, 2, "%")} (κ = ${
                          candleRes.parameters.kappaMeanRev
                        })`,
                      ],
                    ].map(([k, v]) => (
                      <div key={k} className="py-2.5 flex items-baseline justify-between gap-4">
                        <dt className="text-muted">{k}</dt>
                        <dd className="font-mono text-[12px] font-medium text-ink tabular-nums text-right">
                          {v}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>

                <div className="card p-5 sm:p-6 flex flex-col justify-between">
                  <div>
                    <p className="micro-label">Catatan Analis Kuantitatif</p>
                    <h3 className="font-display text-xl font-semibold text-ink mt-0.5 mb-4">
                      Interpretasi Distribusi {assetData.baseAsset}
                    </h3>
                    <ul className="space-y-3 text-[13.5px] leading-6 text-ink/90">
                      {(candleRes.notes || []).map((note, idx) => (
                        <li key={idx} className="flex items-start gap-2.5">
                          <span className="mt-2 w-1.5 h-1.5 rounded-full bg-pine-600 shrink-0" />
                          <span>{note}</span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  {assetData.cryptorank && (
                    <div className="mt-5 pt-4 border-t border-line flex flex-wrap items-center justify-between gap-2 font-mono text-[11px] text-muted">
                      <span>Konteks CryptoRank #{assetData.cryptorank.rank || "—"}</span>
                      <span>
                        Market Cap {fmtUsdCompact(assetData.cryptorank.marketCap)}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </section>
      )}

      {/* ================================================================== */}
      {/* TAB 3 — DETEKTOR SPOOFING ORDER BOOK & LAB SIMULATOR PDE           */}
      {/* ================================================================== */}
      {activeTab === "spoofing" && (
        <section className="mt-7 space-y-6 animate-fade-up">
          {/* Mode Switcher + Live API Snapshot Toolbar */}
          <div className="card p-4 sm:p-5 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
            <div className="inline-flex rounded-full border border-lineStrong bg-paper p-1 self-start">
              <button
                type="button"
                onClick={() => {
                  setSimMode("orderbook");
                }}
                className={`rounded-full px-4 py-1.5 text-[12.5px] font-medium transition-colors ${
                  simMode === "orderbook"
                    ? "bg-pine-800 text-paper"
                    : "text-muted hover:text-ink"
                }`}
              >
                Order Book & Deteksi Spoofing (dt = detik)
              </button>
              <button
                type="button"
                onClick={() => {
                  setWsEnabled(false);
                  setSimMode("direct");
                }}
                className={`rounded-full px-4 py-1.5 text-[12.5px] font-medium transition-colors ${
                  simMode === "direct"
                    ? "bg-pine-800 text-paper"
                    : "text-muted hover:text-ink"
                }`}
              >
                Sandbox Rumus Langsung (μ, D, t)
              </button>
            </div>

            {simMode === "orderbook" && (
              <div className="flex flex-wrap items-center gap-2.5">
                <select
                  value={selectedSymbol}
                  onChange={(e) => setSelectedSymbol(e.target.value)}
                  className="focus-ring rounded-full border border-lineStrong bg-paper px-3.5 py-2 font-mono text-[12px] font-semibold text-ink"
                >
                  {Array.from(
                    new Set([
                      selectedSymbol,
                      ...(universeData?.data || []).map((r) => r.symbol),
                    ])
                  ).map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>

                <button
                  type="button"
                  onClick={() => handlePullApiSnapshot(selectedSymbol)}
                  disabled={snapBusy}
                  className="btn-ghost !py-2 !px-3.5 text-[12px]"
                >
                  <RefreshCw size={13} className={snapBusy ? "animate-spin" : ""} />
                  Ambil Snapshot API
                </button>

                <button
                  type="button"
                  onClick={() => setWsEnabled((v) => !v)}
                  className={
                    wsEnabled
                      ? "btn-primary !py-2 !px-3.5 text-[12px]"
                      : "btn-ghost !py-2 !px-3.5 text-[12px]"
                  }
                >
                  {wsEnabled ? <Radio size={13} className="animate-pulse" /> : <Radio size={13} />}
                  {wsEnabled
                    ? wsStatus === "live"
                      ? "Hentikan Live WS"
                      : "Menghubungkan…"
                    : "Pantau Live WS"}
                </button>
              </div>
            )}
          </div>

          {/* Optional live WS / API snapshot feedback banners */}
          {wsMessage && simMode === "orderbook" && (
            <div className="flex items-start gap-2.5 rounded-xl border border-gold-300 bg-gold-100/70 px-4 py-3 text-[12.5px] text-gold-700">
              <WifiOff size={15} className="mt-0.5 shrink-0" />
              <span>{wsMessage}</span>
            </div>
          )}

          {snapMeta && simMode === "orderbook" && (activePresetId === "api_snapshot" || activePresetId === "live_ws") && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-pine-200 bg-pine-50 px-4 py-2.5 text-[12px] text-pine-800">
              <div className="flex items-center gap-2">
                <Info size={14} className="shrink-0 text-pine-600" />
                <span>
                  <strong>{snapMeta.symbol}</strong> · sumber{" "}
                  <code className="font-mono text-[11px]">{snapMeta.source}</code>
                  {snapMeta.midPrice ? ` · mid $${fmtPrice(snapMeta.midPrice)}` : ""}
                  {snapMeta.spreadBps != null
                    ? ` · spread ${fmtNum(snapMeta.spreadBps, 2)} bps`
                    : ""}
                  {snapMeta.note ? ` — ${snapMeta.note}` : ""}
                </span>
              </div>
              <span className="font-mono text-[10px] uppercase tracking-micro">
                {snapMeta.live ? "DATA LIVE" : "PROXY CANDLE"}
              </span>
            </div>
          )}

          {/* Scenario Preset Cards */}
          {simMode === "orderbook" ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5">
              {obPresets.map((pr) => {
                const active = activePresetId === pr.id;
                return (
                  <button
                    key={pr.id}
                    type="button"
                    onClick={() => {
                      setWsEnabled(false);
                      setActivePresetId(pr.id);
                      setObParams({ ...pr.params, midPrice: undefined });
                    }}
                    className={`focus-ring text-left rounded-xl border p-3 transition-colors ${
                      active
                        ? "bg-pine-800 text-paper border-pine-800"
                        : "bg-card text-ink border-line hover:border-pine-400"
                    }`}
                  >
                    <span className="block font-sans text-[12.5px] font-semibold leading-tight">
                      {pr.label}
                    </span>
                    <span
                      className={`block text-[10.5px] mt-1 leading-snug ${
                        active ? "text-pine-100" : "text-muted"
                      }`}
                    >
                      {pr.subtitle}
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {DIRECT_PRESETS.map((pr) => (
                <button
                  key={pr.id}
                  type="button"
                  onClick={() => setDirectParams({ ...pr.values })}
                  className="focus-ring text-left rounded-xl border border-line bg-card hover:border-pine-400 p-3.5 transition-colors"
                >
                  <span className="block text-[12.5px] font-semibold text-ink">{pr.label}</span>
                  <span className="block text-[11px] text-muted mt-1">{pr.sub}</span>
                </button>
              ))}
            </div>
          )}

          {simError && (
            <div className="card p-4 border-[#E3B7AC] bg-[#F6E4E0] text-[#8A3324] text-sm">
              {simError}
            </div>
          )}

          {/* Main Simulator Grid: Sliders (left) + PDF & Evolution (right) */}
          <div className="grid lg:grid-cols-12 gap-6">
            {/* Controls Column */}
            <div className="card p-5 sm:p-6 lg:col-span-4 space-y-4">
              <div className="flex items-center justify-between">
                <p className="micro-label">
                  {simMode === "orderbook" ? "Parameter Mikrostruktur" : "Parameter PDE Langsung"}
                </p>
                {simBusy && (
                  <span className="font-mono text-[10px] text-muted animate-soft-blink">
                    Menghitung…
                  </span>
                )}
              </div>

              {simMode === "orderbook" ? (
                <div className="space-y-3.5">
                  <SliderField
                    label="Kedalaman Bid (Limit Beli)"
                    value={obParams.bidDepth}
                    min={obStep}
                    max={obMaxScale}
                    step={obStep}
                    display={fmtNum(obParams.bidDepth, obDigits)}
                    onChange={(v) => {
                      setActivePresetId("custom");
                      setObParams((p) => ({ ...p, bidDepth: v }));
                    }}
                  />
                  <SliderField
                    label="Kedalaman Ask (Limit Jual)"
                    value={obParams.askDepth}
                    min={obStep}
                    max={obMaxScale}
                    step={obStep}
                    display={fmtNum(obParams.askDepth, obDigits)}
                    onChange={(v) => {
                      setActivePresetId("custom");
                      setObParams((p) => ({ ...p, askDepth: v }));
                    }}
                  />
                  <SliderField
                    label="Eksekusi Taker Buy (Beli Riil)"
                    value={obParams.takerBuyVol}
                    min={0}
                    max={obMaxScale}
                    step={obStep}
                    display={fmtNum(obParams.takerBuyVol, obDigits)}
                    onChange={(v) => {
                      setActivePresetId("custom");
                      setObParams((p) => ({ ...p, takerBuyVol: v }));
                    }}
                  />
                  <SliderField
                    label="Eksekusi Taker Sell (Jual Riil)"
                    value={obParams.takerSellVol}
                    min={0}
                    max={obMaxScale}
                    step={obStep}
                    display={fmtNum(obParams.takerSellVol, obDigits)}
                    onChange={(v) => {
                      setActivePresetId("custom");
                      setObParams((p) => ({ ...p, takerSellVol: v }));
                    }}
                  />
                  <SliderField
                    label="Order Bid Di-Cancel (Tarik Bid)"
                    value={obParams.canceledBidVol}
                    min={0}
                    max={obMaxScale}
                    step={obStep}
                    display={fmtNum(obParams.canceledBidVol, obDigits)}
                    onChange={(v) => {
                      setActivePresetId("custom");
                      setObParams((p) => ({ ...p, canceledBidVol: v }));
                    }}
                  />
                  <SliderField
                    label="Order Ask Di-Cancel (Spoof Ask)"
                    value={obParams.canceledAskVol}
                    min={0}
                    max={obMaxScale}
                    step={obStep}
                    display={fmtNum(obParams.canceledAskVol, obDigits)}
                    onChange={(v) => {
                      setActivePresetId("custom");
                      setObParams((p) => ({ ...p, canceledAskVol: v }));
                    }}
                  />
                  <div className="pt-2 border-t border-line space-y-3.5">
                    <SliderField
                      label="Volatilitas σ per Detik"
                      value={obParams.sigmaPerSec}
                      min={0.0002}
                      max={0.0035}
                      step={0.0001}
                      display={`${(obParams.sigmaPerSec * 10000).toFixed(1)} bps/dtk`}
                      onChange={(v) => setObParams((p) => ({ ...p, sigmaPerSec: v }))}
                    />
                    <SliderField
                      label="Horizon Waktu (T)"
                      value={obParams.horizonSec}
                      min={5}
                      max={120}
                      step={5}
                      display={`${obParams.horizonSec} detik`}
                      onChange={(v) => setObParams((p) => ({ ...p, horizonSec: v }))}
                    />
                    <SliderField
                      label="Zona Stabil (±a)"
                      value={obParams.stableBand}
                      min={0.001}
                      max={0.02}
                      step={0.0005}
                      display={`±${(obParams.stableBand * 100).toFixed(2)}%`}
                      onChange={(v) => setObParams((p) => ({ ...p, stableBand: v }))}
                    />
                  </div>
                </div>
              ) : (
                <div className="space-y-3.5">
                  <SliderField
                    label="Drift Tekanan Pasar (μ)"
                    value={directParams.mu}
                    min={-0.6}
                    max={0.6}
                    step={0.01}
                    display={fmtSignedNum(directParams.mu, 2)}
                    onChange={(v) => setDirectParams((p) => ({ ...p, mu: v }))}
                  />
                  <SliderField
                    label="Koefisien Difusi / Varians (D)"
                    value={directParams.diffusion}
                    min={0.001}
                    max={0.6}
                    step={0.002}
                    display={fmtNum(directParams.diffusion, 3)}
                    onChange={(v) => setDirectParams((p) => ({ ...p, diffusion: v }))}
                  />
                  <SliderField
                    label="Gaya Mean-Reversion (κ)"
                    value={directParams.kappa}
                    min={0}
                    max={1.5}
                    step={0.05}
                    display={fmtNum(directParams.kappa, 2)}
                    onChange={(v) => setDirectParams((p) => ({ ...p, kappa: v }))}
                  />
                  <SliderField
                    label="Fat-Tail Crypto (η)"
                    value={directParams.tailEta}
                    min={0}
                    max={1.0}
                    step={0.05}
                    display={fmtNum(directParams.tailEta, 2)}
                    onChange={(v) => setDirectParams((p) => ({ ...p, tailEta: v }))}
                  />
                  <SliderField
                    label="Asimetri Crash (λ_down)"
                    value={directParams.downsideAsym}
                    min={0}
                    max={1.0}
                    step={0.05}
                    display={fmtNum(directParams.downsideAsym, 2)}
                    onChange={(v) => setDirectParams((p) => ({ ...p, downsideAsym: v }))}
                  />
                  <div className="pt-2 border-t border-line space-y-3.5">
                    <SliderField
                      label="Langkah / Horizon Waktu (t)"
                      value={directParams.horizon}
                      min={0.2}
                      max={10.0}
                      step={0.2}
                      display={`${fmtNum(directParams.horizon, 1)} satuan`}
                      onChange={(v) => setDirectParams((p) => ({ ...p, horizon: v }))}
                    />
                    <SliderField
                      label="Zona Stabil (±a)"
                      value={directParams.stableBand}
                      min={0.02}
                      max={0.3}
                      step={0.01}
                      display={`±${(directParams.stableBand * 100).toFixed(0)}%`}
                      onChange={(v) => setDirectParams((p) => ({ ...p, stableBand: v }))}
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Results Column */}
            <div className="lg:col-span-8 space-y-5">
              {simResult?.ok && (
                <>
                  {/* Regime & Spoof Header Card (orderbook mode) */}
                  {simResult.mode === "orderbook" && simResult.microstructure && (
                    <div className="card p-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                      <div>
                        <div className="flex flex-wrap items-center gap-2.5">
                          <VerdictBadge
                            tone={simResult.regimeTone}
                            label={simResult.regimeLabel}
                            size="lg"
                          />
                          <span className="chip border-lineStrong bg-paper text-ink">
                            Spoof Index {Math.round(simResult.microstructure.spoofIndex * 100)}%
                          </span>
                        </div>
                        <p className="mt-2 text-[13px] text-muted">{simResult.regimeSummary}</p>
                      </div>
                      <div className="font-mono text-[11.5px] tabular-nums space-y-1 sm:text-right shrink-0">
                        <div>
                          <span className="text-muted">OBI (Buku): </span>
                          <span
                            className={`font-semibold ${
                              simResult.microstructure.obi >= 0 ? "text-up" : "text-down"
                            }`}
                          >
                            {fmtSignedNum(simResult.microstructure.obi, 2)}
                          </span>
                        </div>
                        <div>
                          <span className="text-muted">TFI (Trade): </span>
                          <span
                            className={`font-semibold ${
                              simResult.microstructure.tfi >= 0 ? "text-up" : "text-down"
                            }`}
                          >
                            {fmtSignedNum(simResult.microstructure.tfi, 2)}
                          </span>
                        </div>
                        <div className="text-faint text-[10.5px]">
                          Cancel B/A: {Math.round(simResult.microstructure.bidCancelRate * 100)}% /{" "}
                          {Math.round(simResult.microstructure.askCancelRate * 100)}%
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Naive scalar vs True PDE Comparison Card (direct mode) */}
                  {simResult.mode === "direct" && simResult.naiveComparison && (
                    <div className="card p-5 border-gold-300 bg-gold-100/50">
                      <p className="micro-label text-gold-700">
                        Perbandingan PDE Konservatif vs Aproksimasi Skalar Naif
                      </p>
                      <div className="mt-2 grid sm:grid-cols-2 gap-4 text-[13px]">
                        <div className="rounded-xl bg-card border border-line p-3.5">
                          <span className="micro-label text-pine-700">
                            PDE Fokker–Planck Penuh (Valid)
                          </span>
                          <p className="mt-1 font-mono text-xl font-semibold text-pine-800 tabular-nums">
                            P(stabil) = {fmtPct(simResult.metrics.pStable, { digits: 1 })}
                          </p>
                          <p className="font-mono text-[11px] text-muted mt-0.5 tabular-nums">
                            Dump {fmtPct(simResult.metrics.pDownside, { digits: 1 })} + Stabil{" "}
                            {fmtPct(simResult.metrics.pStable, { digits: 1 })} + Pump{" "}
                            {fmtPct(simResult.metrics.pUpside, { digits: 1 })} ={" "}
                            {(simResult.metrics.totalMass * 100).toFixed(2)}%
                          </p>
                        </div>
                        <div className="rounded-xl bg-card border border-[#E3B7AC] p-3.5">
                          <span className="micro-label text-down">
                            Rumus Skalar P + (μP + 0.5·D·P)dt
                          </span>
                          <p className="mt-1 font-mono text-xl font-semibold text-down tabular-nums">
                            P_baru = {(simResult.naiveComparison.naiveResult * 100).toFixed(1)}%
                          </p>
                          <p className="text-[11px] text-muted mt-0.5 leading-snug">
                            {simResult.naiveComparison.issues[0]}
                          </p>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* 4 Zone Stat Cards */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3.5">
                    <StatTile
                      label="P(Stabil)"
                      value={fmtPct(simResult.metrics.pStable, { digits: 1 })}
                      sub={`±${(simResult.stableBand * 100).toFixed(
                        simResult.mode === "orderbook" ? 2 : 1
                      )}%`}
                      tone="pine"
                    />
                    <StatTile
                      label="P(Dump / Kiri)"
                      value={fmtPct(simResult.metrics.pDownside, { digits: 1 })}
                      sub={`E[x] ${
                        simResult.mode === "orderbook"
                          ? `${fmtSignedNum(simResult.metrics.meanLogReturn * 10000, 1)} bps`
                          : fmtPct(simResult.metrics.meanLogReturn, { signed: true, digits: 2 })
                      }`}
                      tone="red"
                    />
                    <StatTile
                      label="P(Pump / Kanan)"
                      value={fmtPct(simResult.metrics.pUpside, { digits: 1 })}
                      sub={`Kurtosis ${fmtNum(simResult.metrics.kurtosis, 2)}`}
                      tone="gold"
                    />
                    <StatTile
                      label="P(Crash Ekor)"
                      value={fmtPct(simResult.metrics.pTailCrash, { digits: 1 })}
                      sub={`<-${(simResult.stableBand * 200).toFixed(
                        simResult.mode === "orderbook" ? 2 : 1
                      )}%`}
                      tone="red"
                    />
                  </div>

                  {/* Simulated PDF Curve */}
                  <div className="card p-5 sm:p-6">
                    <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
                      <div>
                        <p className="micro-label">Hasil Solusi Numerik Chang–Cooper</p>
                        <h3 className="font-display text-lg font-semibold text-ink mt-0.5">
                          Kurva Kepadatan P(x, T)
                        </h3>
                      </div>
                      <span className="font-mono text-[11px] text-muted tabular-nums">
                        ∫P(x,T)dx = {fmtNum(simResult.metrics.totalMass, 6)}
                      </span>
                    </div>
                    <PdfCurveChart
                      curve={simResult.curve}
                      stableBand={simResult.stableBand}
                      refPrice={simResult.midPrice || null}
                      unitMode={simResult.mode === "orderbook" ? "micro" : "pct"}
                      height={265}
                    />
                  </div>

                  {/* Time Evolution + Diagnostic Notes */}
                  <div className="grid md:grid-cols-2 gap-5">
                    <div className="card p-5">
                      <p className="micro-label">Dinamika Luruh Spoof vs Tren Riil</p>
                      <h3 className="font-display text-lg font-semibold text-ink mt-0.5 mb-2">
                        Evolusi Zona Sepanjang Waktu
                      </h3>
                      <TimelineChart
                        timeline={simResult.timeline}
                        unitLabel={simResult.mode === "orderbook" ? "s" : ""}
                        height={215}
                      />
                    </div>
                    <div className="card p-5">
                      <p className="micro-label">Pembacaan Mikrostruktur</p>
                      <h3 className="font-display text-lg font-semibold text-ink mt-0.5 mb-3">
                        Ringkasan Diagnostik
                      </h3>
                      <ul className="space-y-2.5 text-[12.5px] leading-5 text-ink/90">
                        {(simResult.notes || []).map((n, i) => (
                          <li key={i} className="flex items-start gap-2">
                            <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-pine-600 shrink-0" />
                            <span>{n}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ================================================================== */}
      {/* TAB 4 — VALIDASI WALK-FORWARD & SPESIFIKASI RUMUS                  */}
      {/* ================================================================== */}
      {activeTab === "backtest" && (
        <section className="mt-7 space-y-6 animate-fade-up">
          {/* Walk-forward runner card */}
          <div className="card p-5 sm:p-6">
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
              <div>
                <p className="micro-label text-gold-600">Audit Empiris Tanpa Lookahead</p>
                <h2 className="font-display text-2xl font-semibold text-ink mt-1">
                  Uji Kalibrasi Walk-Forward Fokker–Planck
                </h2>
                <p className="mt-1.5 text-[13.5px] text-muted max-w-2xl">
                  Menguji seberapa jujur probabilitas zona{" "}
                  <code className="font-mono text-[12px]">P(stabil)</code>,{" "}
                  <code className="font-mono text-[12px]">P(downside)</code>,{" "}
                  <code className="font-mono text-[12px]">P(upside)</code>, dan{" "}
                  <code className="font-mono text-[12px]">P(crash)</code> terhadap realisasi return
                  masa depan secara kronologis pada data API yang tersedia.
                </p>
              </div>
              <button
                type="button"
                onClick={handleRunBacktest}
                disabled={btBusy}
                className="btn-primary self-start md:self-auto"
              >
                <Play size={14} />
                {btBusy ? "Menjalankan Walk-Forward…" : "Jalankan Uji Walk-Forward"}
              </button>
            </div>

            <div className="mt-5 pt-5 border-t border-line grid grid-cols-2 sm:grid-cols-4 gap-4">
              <label className="block">
                <span className="micro-label block mb-1.5">Horizon Prediksi</span>
                <select
                  value={btParams.horizonDays}
                  onChange={(e) =>
                    setBtParams((p) => ({ ...p, horizonDays: Number(e.target.value) }))
                  }
                  className="w-full rounded-xl border border-lineStrong bg-paper px-3 py-2 font-mono text-[12px] text-ink"
                >
                  <option value={3}>3 hari</option>
                  <option value={7}>7 hari</option>
                  <option value={14}>14 hari</option>
                  <option value={30}>30 hari</option>
                </select>
              </label>

              <label className="block">
                <span className="micro-label block mb-1.5">Zona Stabil (±a)</span>
                <select
                  value={btParams.stableBand}
                  onChange={(e) =>
                    setBtParams((p) => ({ ...p, stableBand: Number(e.target.value) }))
                  }
                  className="w-full rounded-xl border border-lineStrong bg-paper px-3 py-2 font-mono text-[12px] text-ink"
                >
                  {BAND_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="micro-label block mb-1.5">Jarak Anchor</span>
                <select
                  value={btParams.stepDays}
                  onChange={(e) =>
                    setBtParams((p) => ({ ...p, stepDays: Number(e.target.value) }))
                  }
                  className="w-full rounded-xl border border-lineStrong bg-paper px-3 py-2 font-mono text-[12px] text-ink"
                >
                  <option value={5}>Setiap 5 hari</option>
                  <option value={7}>Setiap 7 hari</option>
                  <option value={14}>Setiap 14 hari</option>
                </select>
              </label>

              <label className="block">
                <span className="micro-label block mb-1.5">Jendela Histori</span>
                <select
                  value={btParams.lookbackDays}
                  onChange={(e) =>
                    setBtParams((p) => ({ ...p, lookbackDays: Number(e.target.value) }))
                  }
                  className="w-full rounded-xl border border-lineStrong bg-paper px-3 py-2 font-mono text-[12px] text-ink"
                >
                  <option value={180}>180 candle</option>
                  <option value={240}>240 candle</option>
                  <option value={365}>365 candle</option>
                </select>
              </label>
            </div>
          </div>

          {btError && (
            <div className="card p-5 border-[#E3B7AC] bg-[#F6E4E0] text-[#8A3324] text-sm">
              {btError}
            </div>
          )}

          {btResult?.ok && (
            <div className="space-y-6">
              {/* Verdict Banner */}
              <div
                className={`card p-5 flex items-start gap-3.5 ${
                  btResult.verdict.status === "TERKALIBRASI_BAIK"
                    ? "border-pine-200 bg-pine-50"
                    : "border-gold-300 bg-gold-100/70"
                }`}
              >
                {btResult.verdict.status === "TERKALIBRASI_BAIK" ? (
                  <CheckCircle2 size={20} className="text-up mt-0.5 shrink-0" />
                ) : (
                  <ShieldCheck size={20} className="text-gold-700 mt-0.5 shrink-0" />
                )}
                <div>
                  <p className="font-sans text-[14px] font-semibold text-ink">
                    {btResult.verdict.label} · {btResult.samples} sampel walk-forward (
                    {btResult.params.symbolsEvaluated} koin · sumber {btResult.source})
                  </p>
                  <p className="mt-1 text-[13px] text-muted">{btResult.verdict.reason}</p>
                </div>
              </div>

              {/* Metrics */}
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatTile
                  label="Brier Score P(Stabil)"
                  value={fmtNum(btResult.scores.brierStable, 4)}
                  sub={`Baseline naif ${fmtNum(btResult.scores.baselineBrierStable, 4)} (makin kecil makin baik)`}
                  tone="pine"
                />
                <StatTile
                  label="Brier Skill Score"
                  value={fmtPct(btResult.scores.brierSkillScore, { signed: true, digits: 1 })}
                  sub="Keunggulan relatif vs rata-rata historis"
                  tone={btResult.scores.brierSkillScore >= 0 ? "pine" : "red"}
                />
                <StatTile
                  label="Brier Multi-Zona (3 Kelas)"
                  value={fmtNum(btResult.scores.brierMultiZone, 4)}
                  sub={`Baseline ${fmtNum(btResult.scores.baselineBrierMultiZone, 4)}`}
                  tone="gold"
                />
                <StatTile
                  label="Selisih Kalibrasi Stabil"
                  value={`${fmtNum(btResult.scores.calibrationGapStable * 100, 1)} pp`}
                  sub="|Rata-rata prediksi − frekuensi aktual|"
                  tone="ink"
                />
              </div>

              {/* Coverage BarChart + Reliability Table */}
              <div className="grid lg:grid-cols-2 gap-6">
                <div className="card p-5 sm:p-6">
                  <p className="micro-label">Cakupan Empiris 4 Zona</p>
                  <h3 className="font-display text-xl font-semibold text-ink mt-0.5 mb-4">
                    Rata-rata Prediksi PDE vs Frekuensi Aktual
                  </h3>
                  <div style={{ width: "100%", height: 245 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart
                        data={btResult.coverage.map((c) => ({
                          zone: c.zone,
                          Prediksi: Number((c.predicted * 100).toFixed(1)),
                          Aktual: Number((c.actual * 100).toFixed(1)),
                        }))}
                        margin={{ top: 8, right: 12, left: -8, bottom: 4 }}
                      >
                        <CartesianGrid stroke="#E8E5DE" strokeDasharray="3 3" vertical={false} />
                        <XAxis
                          dataKey="zone"
                          tick={{ fill: "#68716A", fontSize: 10.5 }}
                          stroke="#D9D5CB"
                        />
                        <YAxis
                          tickFormatter={(v) => `${v}%`}
                          tick={{
                            fill: "#9AA096",
                            fontSize: 10,
                            fontFamily: "IBM Plex Mono, monospace",
                          }}
                          stroke="#D9D5CB"
                        />
                        <Tooltip
                          formatter={(v) => `${v}%`}
                          contentStyle={{
                            backgroundColor: "#FFFFFF",
                            borderColor: "#D9D5CB",
                            borderRadius: "12px",
                            fontSize: "12px",
                            fontFamily: "IBM Plex Mono, monospace",
                          }}
                        />
                        <Legend wrapperStyle={{ fontSize: "11px" }} />
                        <Bar dataKey="Prediksi" fill="#144338" radius={[4, 4, 0, 0]} />
                        <Bar dataKey="Aktual" fill="#B18A44" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="card p-5 sm:p-6">
                  <p className="micro-label">Tabel Reliabilitas P(Stabil)</p>
                  <h3 className="font-display text-xl font-semibold text-ink mt-0.5 mb-4">
                    Kalibrasi per Kelompok Probabilitas
                  </h3>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Rentang</th>
                        <th>Sampel</th>
                        <th>Prediksi Rata-rata</th>
                        <th>Realisasi Aktual</th>
                      </tr>
                    </thead>
                    <tbody>
                      {btResult.reliabilityBins.map((b) => (
                        <tr key={b.bin}>
                          <td className="font-mono text-[12px] text-ink">{b.bin}</td>
                          <td className="font-mono text-[12px] text-muted tabular-nums">
                            {b.count}
                          </td>
                          <td className="font-mono text-[12px] text-pine-700 tabular-nums">
                            {b.predicted !== null ? fmtPct(b.predicted, { digits: 1 }) : "—"}
                          </td>
                          <td className="font-mono text-[12px] text-ink tabular-nums">
                            {b.actual !== null ? fmtPct(b.actual, { digits: 1 }) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Per-symbol walk-forward breakdown */}
              {Array.isArray(btResult.perSymbol) && btResult.perSymbol.length > 0 && (
                <div className="card overflow-hidden">
                  <div className="px-5 pt-5 pb-3 border-b border-line flex flex-wrap items-baseline justify-between gap-2">
                    <div>
                      <p className="micro-label">Rincian per Aset</p>
                      <h3 className="font-display text-lg font-semibold text-ink mt-0.5">
                        Akurasi Brier P(Stabil) Diurutkan dari Paling Presisi
                      </h3>
                    </div>
                    <span className="font-mono text-[10.5px] text-muted">
                      Klik aset untuk inspeksi kurva
                    </span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Simbol</th>
                          <th>Titik Uji</th>
                          <th>Prediksi P(Stabil)</th>
                          <th>Aktual Stabil</th>
                          <th>Brier Score</th>
                        </tr>
                      </thead>
                      <tbody>
                        {btResult.perSymbol.map((ps) => (
                          <tr
                            key={ps.symbol}
                            className="row-link"
                            onClick={() => openAssetDetail(ps.symbol)}
                          >
                            <td className="font-mono text-[12.5px] font-semibold text-ink">
                              {ps.symbol}
                            </td>
                            <td className="font-mono text-[12px] text-muted tabular-nums">
                              {ps.samples}
                            </td>
                            <td className="font-mono text-[12px] text-pine-700 tabular-nums">
                              {fmtPct(ps.predStable, { digits: 1 })}
                            </td>
                            <td className="font-mono text-[12px] text-ink tabular-nums">
                              {fmtPct(ps.actualStable, { digits: 1 })}
                            </td>
                            <td className="font-mono text-[12px] font-medium text-ink tabular-nums">
                              {fmtNum(ps.brierStable, 4)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Mathematical Reference & Honest Caveats */}
          <div className="card p-6 sm:p-7 space-y-5">
            <div className="flex items-center gap-2 text-gold-600">
              <BookOpen size={15} />
              <span className="micro-label text-gold-600">Dokumentasi Rumus & Batasan Model</span>
            </div>
            <h3 className="font-display text-2xl font-semibold text-ink">
              Persamaan Fokker–Planck 1D & Skema Numerik Chang–Cooper
            </h3>

            <div className="space-y-2.5">
              <div className="formula-block">
                ∂P(x,t)/∂t = −∂/∂x [ μ(x,t) · P(x,t) ] + (1/2) · ∂²/∂x² [ D(x,t) · P(x,t) ]
              </div>
              <div className="formula-block">
                J(x,t) = [ μ(x,t) − (1/2)·∂D/∂x ] · P(x,t) − (1/2)·D(x,t)·∂P/∂x ,   B(w) = w / (e^w − 1)
              </div>
              <div className="formula-block">
                P_stabil(T) = ∫[-a,+a] P(x,T) dx  ·  P_down(T) = ∫(-∞,-a) P(x,T) dx  ·  P_up(T) = ∫(+a,+∞) P(x,T) dx
              </div>
            </div>

            <div className="grid md:grid-cols-2 gap-6 pt-2 text-[13.5px] leading-6 text-ink/90">
              <div>
                <h4 className="font-sans font-semibold text-ink mb-1.5">
                  Adaptasi Khusus Pasar Crypto
                </h4>
                <ul className="space-y-1.5 text-muted">
                  <li>
                    • <strong>Difusi berekor tebal:</strong> D(x) = D₀·(1 + λ_down·max(0, −u) +
                    η·u²) menghasilkan distribusi setara Student-t dengan ekor crash kiri lebih
                    tebal daripada Gaussian standar.
                  </li>
                  <li>
                    • <strong>Volatilitas wick intraday:</strong> D₀ menggabungkan EWMA return
                    dengan varians Parkinson high-low agar jarum likuidasi ikut terukur.
                  </li>
                  <li>
                    • <strong>Pemisahan spoofing vs eksekusi riil:</strong> membandingkan Order
                    Book Imbalance + cancel rate terhadap Trade Flow Imbalance dengan peluruhan
                    eksponensial τ.
                  </li>
                </ul>
              </div>
              <div>
                <h4 className="font-sans font-semibold text-ink mb-1.5 flex items-center gap-1.5">
                  <ShieldAlert size={15} className="text-gold-600" /> Batasan yang Wajib Dipahami
                </h4>
                <ul className="space-y-1.5 text-muted">
                  <li>
                    • Solver PDE eksak secara numerik, namun pemetaan fitur pasar ke μ(x,t) dan
                    D(x,t) tetap model heuristik — bukan jaminan pergerakan harga masa depan.
                  </li>
                  <li>
                    • Deteksi spoofing presisi tinggi membutuhkan aliran order book + cancel
                    berurutan (Live WS atau snapshot berulang), bukan satu candle harian saja.
                  </li>
                  <li>
                    • Kejadian diskrit mendadak (peretasan protokol, delisting, atau likuidasi
                    berantai ekstrem) dapat melompati asumsi difusi kontinu.
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
