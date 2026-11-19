import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  ArrowUpRight,
  CheckCircle2,
  FlaskConical,
  GitCompare,
  Info,
  Play,
  Radio,
  RefreshCw,
  Search,
  ShieldCheck,
  Sliders,
  WifiOff,
} from "lucide-react";
import StatTile from "../components/StatTile";
import VerdictBadge from "../components/VerdictBadge";
import {
  fetchFpV2Asset,
  fetchFpV2Spec,
  fetchFpV2Universe,
  runFpV2Backtest,
  simulateFpV2,
} from "../lib/api";
import { fmtPct, fmtPrice } from "../lib/format";
import { subscribeOrderbookStreamV2 } from "../lib/liveOrderbookV2";
import { useLiveTickers } from "../lib/useLiveTickers";

const TABS = [
  { id: "universe", label: "01 · Konsensus Universe", icon: GitCompare },
  { id: "asset", label: "02 · Aset v1 vs v2", icon: Activity },
  { id: "spoofing", label: "03 · Order-Book v1 vs v2", icon: Sliders },
  { id: "backtest", label: "04 · Adu Akurasi v1 vs v2", icon: FlaskConical },
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

const CONSENSUS_FILTERS = [
  { id: "ALL", label: "Semua" },
  { id: "STRONG", label: "Sepakat Kuat" },
  { id: "SAME_DIR", label: "Searah (v1 = v2)" },
  { id: "DIFF", label: "Filter v2 / Berbeda" },
];

const FALLBACK_PRESETS = [
  {
    id: "calm",
    label: "Pasar Tenang",
    subtitle: "Uji kesamaan baseline (v1 ≈ v2)",
    params: {
      bidDepth: 120,
      askDepth: 120,
      farBidDepth: 0,
      farAskDepth: 0,
      wallDistancePct: 0.008,
      takerBuyVol: 45,
      takerSellVol: 45,
      canceledBidVol: 10,
      canceledAskVol: 10,
      spreadBps: 8,
      priceChangeBps: null,
      sigmaPerSec: 0.0005,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
  {
    id: "distant_wall",
    label: "Tembok Jauh 0,8%",
    subtitle: "v1 tertekan vs v2 redam e^(−λd)",
    params: {
      bidDepth: 115,
      askDepth: 110,
      farBidDepth: 0,
      farAskDepth: 480,
      wallDistancePct: 0.008,
      takerBuyVol: 46,
      takerSellVol: 44,
      canceledBidVol: 8,
      canceledAskVol: 12,
      spreadBps: 8,
      priceChangeBps: null,
      sigmaPerSec: 0.0006,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
  {
    id: "iceberg_buy",
    label: "Absorpsi Iceberg Beli",
    subtitle: "v1 baca dump vs v2 tangkap absorpsi",
    params: {
      bidDepth: 150,
      askDepth: 105,
      farBidDepth: 0,
      farAskDepth: 0,
      wallDistancePct: 0.008,
      takerBuyVol: 16,
      takerSellVol: 96,
      canceledBidVol: 6,
      canceledAskVol: 10,
      spreadBps: 8,
      priceChangeBps: 0.0,
      sigmaPerSec: 0.0008,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
  {
    id: "taker_vs_wall",
    label: "Taker Buy vs Tembok Ask",
    subtitle: "Transaksi nyata lawan limit jual tebal",
    params: {
      bidDepth: 55,
      askDepth: 320,
      farBidDepth: 0,
      farAskDepth: 0,
      wallDistancePct: 0.008,
      takerBuyVol: 92,
      takerSellVol: 24,
      canceledBidVol: 6,
      canceledAskVol: 12,
      spreadBps: 12,
      priceChangeBps: 18.0,
      sigmaPerSec: 0.0009,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
  {
    id: "spoof_sell",
    label: "Spoofing Jual (Dekat)",
    subtitle: "v1 & v2 sama-sama deteksi spoof",
    params: {
      bidDepth: 45,
      askDepth: 260,
      farBidDepth: 0,
      farAskDepth: 0,
      wallDistancePct: 0.008,
      takerBuyVol: 38,
      takerSellVol: 42,
      canceledBidVol: 8,
      canceledAskVol: 340,
      spreadBps: 10,
      priceChangeBps: null,
      sigmaPerSec: 0.0007,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
  {
    id: "real_dump",
    label: "Panic Dump Riil",
    subtitle: "Konfirmasi SEPAKAT KUAT dump",
    params: {
      bidDepth: 45,
      askDepth: 220,
      farBidDepth: 0,
      farAskDepth: 0,
      wallDistancePct: 0.008,
      takerBuyVol: 18,
      takerSellVol: 95,
      canceledBidVol: 15,
      canceledAskVol: 25,
      spreadBps: 14,
      priceChangeBps: -26.0,
      sigmaPerSec: 0.0011,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
  {
    id: "real_pump",
    label: "Breakout Beli Riil",
    subtitle: "Konfirmasi SEPAKAT KUAT naik",
    params: {
      bidDepth: 230,
      askDepth: 55,
      farBidDepth: 0,
      farAskDepth: 0,
      wallDistancePct: 0.008,
      takerBuyVol: 95,
      takerSellVol: 22,
      canceledBidVol: 20,
      canceledAskVol: 12,
      spreadBps: 12,
      priceChangeBps: 24.0,
      sigmaPerSec: 0.0009,
      horizonSec: 30,
      stableBand: 0.004,
    },
  },
  {
    id: "whipsaw",
    label: "Whipsaw / Makro",
    subtitle: "Konfirmasi SEPAKAT KUAT 2 arah",
    params: {
      bidDepth: 100,
      askDepth: 100,
      farBidDepth: 0,
      farAskDepth: 0,
      wallDistancePct: 0.008,
      takerBuyVol: 85,
      takerSellVol: 85,
      canceledBidVol: 45,
      canceledAskVol: 45,
      spreadBps: 22,
      priceChangeBps: null,
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

function fmtSigned(v, digits = 2, suffix = "") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}${suffix}`;
}

function cleanSimPayload(params) {
  const out = {};
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== null && v !== undefined && !(typeof v === "number" && Number.isNaN(v))) {
      out[k] = v;
    }
  });
  return out;
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

function ZoneBar({ m }) {
  if (!m) return null;
  const d = Math.max(0, Math.min(100, (m.pDownside || 0) * 100));
  const s = Math.max(0, Math.min(100, (m.pStable || 0) * 100));
  const u = Math.max(0, Math.min(100, (m.pUpside || 0) * 100));
  return (
    <div className="space-y-1">
      <div className="h-2.5 w-full rounded-full overflow-hidden flex bg-paper border border-line">
        <div style={{ width: `${d}%` }} className="bg-down" title={`Dump ${d.toFixed(1)}%`} />
        <div style={{ width: `${s}%` }} className="bg-pine-700" title={`Stabil ${s.toFixed(1)}%`} />
        <div style={{ width: `${u}%` }} className="bg-gold-500" title={`Pump ${u.toFixed(1)}%`} />
      </div>
      <div className="flex justify-between font-mono text-[10.5px] tabular-nums">
        <span className="text-down">Dump {d.toFixed(1)}%</span>
        <span className="font-semibold text-pine-800">Stabil {s.toFixed(1)}%</span>
        <span className="text-gold-700">Naik {u.toFixed(1)}%</span>
      </div>
    </div>
  );
}

function MicroPriceGauge({ ms, hasRealPrice }) {
  if (!ms) return null;
  const pct = Math.min(98, Math.max(2, (ms.microImbalance ?? 0.5) * 100));
  const rawPct = Math.min(98, Math.max(2, (ms.rawMicroImbalance ?? 0.5) * 100));
  const shielded = Math.abs(pct - rawPct) >= 3;
  const shift = ms.microShiftBps ?? 0;

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between font-mono text-[11px] tabular-nums">
        <span className="text-ink font-semibold">
          {hasRealPrice ? `Micro $${fmtPrice(ms.microPrice)}` : `Bobot I = ${fmtNum(pct, 1)}%`}
        </span>
        <span
          className={`font-semibold ${
            shift > 0.3 ? "text-up" : shift < -0.3 ? "text-down" : "text-muted"
          }`}
        >
          {fmtSigned(shift, 2, " bps")} vs Mid
        </span>
      </div>
      <div className="relative h-2.5 rounded-full bg-gradient-to-r from-[#F6E4E0] via-paper to-pine-100 border border-lineStrong">
        <span className="absolute top-0 bottom-0 w-px bg-muted/60" style={{ left: "50%" }} />
        {shielded && (
          <span
            className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-2 h-2 rounded-full border border-down bg-paper"
            style={{ left: `${rawPct}%` }}
            title={`Tanpa filter cancel: ${fmtNum(rawPct, 1)}%`}
          />
        )}
        <span
          className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-3.5 h-3.5 rounded-full bg-pine-800 border-2 border-paper shadow-sm transition-all"
          style={{ left: `${pct}%` }}
        />
      </div>
      <div className="flex justify-between font-mono text-[9.5px] text-muted tabular-nums">
        <span>Bid {hasRealPrice ? `$${fmtPrice(ms.bestBid)}` : ""}</span>
        <span>Spread {fmtNum(ms.spreadBps, 1)} bps</span>
        <span>{hasRealPrice ? `$${fmtPrice(ms.bestAsk)} ` : ""}Ask</span>
      </div>
    </div>
  );
}

/**
 * Overlay PDF chart rendering Model v1 (gold dashed), Model v2 (pine solid),
 * and shaded intersection area min(P_v1, P_v2) = visual similarity.
 */
function OverlayPdfChart({ overlayCurve, stableBand, unitMode = "pct", height = 285 }) {
  if (!Array.isArray(overlayCurve) || overlayCurve.length === 0) return null;
  const bandPct = (stableBand || 0.05) * 100;
  const fmtX = (v) =>
    unitMode === "micro"
      ? `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`
      : `${v > 0 ? "+" : ""}${Number(v).toFixed(0)}%`;

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={overlayCurve} margin={{ top: 10, right: 14, left: -12, bottom: 4 }}>
          <CartesianGrid stroke="#E8E5DE" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="xPct"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={fmtX}
            tick={{ fill: "#68716A", fontSize: 10.5, fontFamily: "IBM Plex Mono, monospace" }}
            stroke="#D9D5CB"
          />
          <YAxis
            tick={{ fill: "#9AA096", fontSize: 10, fontFamily: "IBM Plex Mono, monospace" }}
            stroke="#D9D5CB"
            width={44}
          />
          <Tooltip
            labelFormatter={(lbl) => `Log-return ${fmtSigned(lbl, unitMode === "micro" ? 3 : 2, "%")}`}
            formatter={(val, name) => [Number(val).toFixed(4), name]}
            contentStyle={{
              backgroundColor: "#FFFFFF",
              borderColor: "#D9D5CB",
              borderRadius: "12px",
              fontSize: "11.5px",
              fontFamily: "IBM Plex Mono, monospace",
            }}
          />
          <Legend wrapperStyle={{ fontSize: "11px", fontFamily: "IBM Plex Mono, monospace" }} />
          <ReferenceLine x={-bandPct} stroke="#B3402F" strokeDasharray="4 4" />
          <ReferenceLine x={0} stroke="#68716A" strokeDasharray="2 3" />
          <ReferenceLine x={bandPct} stroke="#144338" strokeDasharray="4 4" />
          <Area
            type="monotone"
            dataKey="common"
            name="Irisan Kesamaan (v1 ∩ v2)"
            stroke="none"
            fill="#144338"
            fillOpacity={0.14}
            isAnimationActive={false}
          />
          <Area
            type="monotone"
            dataKey="v1"
            name="Model v1 (Baseline Asli)"
            stroke="#B18A44"
            strokeWidth={2}
            strokeDasharray="5 3"
            fill="none"
            isAnimationActive={false}
          />
          <Area
            type="monotone"
            dataKey="v2"
            name="Model v2 (3 Pilar Baru)"
            stroke="#144338"
            strokeWidth={2.4}
            fill="none"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function ConsensusBanner({ cmp }) {
  if (!cmp) return null;
  const bannerStyle =
    cmp.status === "SEPAKAT_KUAT" || cmp.status === "SEPAKAT_ARAH"
      ? "border-pine-200 bg-pine-50"
      : cmp.status === "FILTER_V2_AKTIF"
      ? "border-gold-300 bg-gold-100/70"
      : "border-[#E3B7AC] bg-[#F6E4E0]/70";

  return (
    <div className={`card p-4 sm:p-5 ${bannerStyle}`}>
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <VerdictBadge tone={cmp.tone} label={cmp.label} size="lg" />
            <span className="chip border-lineStrong bg-card text-ink">
              Overlap Kurva {(cmp.pdfOverlap * 100).toFixed(1)}%
            </span>
            <span className="chip border-lineStrong bg-card text-ink">
              Kesamaan Zona {(cmp.zoneSimilarity * 100).toFixed(1)}%
            </span>
          </div>
          <p className="text-[13px] text-ink/90 leading-relaxed">{cmp.reason}</p>
        </div>

        <div className="font-mono text-[11px] tabular-nums space-y-1 md:text-right shrink-0">
          <div>
            <span className="text-muted">Δ P(Stabil) v2−v1: </span>
            <span
              className={`font-semibold ${
                cmp.deltaPStable > 0.005
                  ? "text-up"
                  : cmp.deltaPStable < -0.005
                  ? "text-down"
                  : "text-ink"
              }`}
            >
              {fmtSigned(cmp.deltaPStable * 100, 1, " pp")}
            </span>
          </div>
          <div>
            <span className="text-muted">Δ Dump / Naik: </span>
            <span className="text-ink">
              {fmtSigned(cmp.deltaPDownside * 100, 1, " pp")} /{" "}
              {fmtSigned(cmp.deltaPUpside * 100, 1, " pp")}
            </span>
          </div>
          <div className="text-faint text-[10.5px]">
            Hellinger {(cmp.hellingerSimilarity * 100).toFixed(1)}% · ΔE[x]{" "}
            {fmtSigned(cmp.deltaMeanBps, 1, " bps")}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function FokkerPlanckV2Page() {
  const { symbol: routeSymbol } = useParams();
  const navigate = useNavigate();

  const [activeTab, setActiveTab] = useState(routeSymbol ? "asset" : "universe");
  const [spec, setSpec] = useState(null);

  const [horizonDays, setHorizonDays] = useState(7);
  const [stableBand, setStableBand] = useState(0.05);

  // Tab 1 — Side-by-side Universe
  const [uniData, setUniData] = useState(null);
  const [uniLoading, setUniLoading] = useState(true);
  const [uniError, setUniError] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [consensusFilter, setConsensusFilter] = useState("ALL");

  // Tab 2 — Side-by-side Asset
  const [selectedSymbol, setSelectedSymbol] = useState(
    routeSymbol ? routeSymbol.toUpperCase() : "BTCUSDT"
  );
  const [customSymInput, setCustomSymInput] = useState("");
  const [assetData, setAssetData] = useState(null);
  const [assetLoading, setAssetLoading] = useState(false);
  const [assetError, setAssetError] = useState(null);

  // Tab 3 — Side-by-side Order-book & Spoofing Lab
  const [activePresetId, setActivePresetId] = useState("distant_wall");
  const [obParams, setObParams] = useState(FALLBACK_PRESETS[1].params);
  const [simData, setSimData] = useState(null);
  const [simBusy, setSimBusy] = useState(false);
  const [simError, setSimError] = useState(null);
  const [snapBusy, setSnapBusy] = useState(false);
  const [snapMeta, setSnapMeta] = useState(null);

  const [wsEnabled, setWsEnabled] = useState(false);
  const [wsStatus, setWsStatus] = useState("idle");
  const [wsMessage, setWsMessage] = useState(null);
  const wsUnsubRef = useRef(null);

  // Tab 4 — Walk-Forward Showdown (v1 vs v2)
  const [btParams, setBtParams] = useState({
    horizonDays: 7,
    stableBand: 0.05,
    stepDays: 7,
    lookbackDays: 240,
    limit: 16,
  });
  const [btData, setBtData] = useState(null);
  const [btBusy, setBtBusy] = useState(false);
  const [btError, setBtError] = useState(null);

  useEffect(() => {
    if (routeSymbol) {
      const clean = routeSymbol.toUpperCase();
      setSelectedSymbol(clean.endsWith("USDT") ? clean : `${clean}USDT`);
      setActiveTab("asset");
    }
  }, [routeSymbol]);

  useEffect(() => {
    let alive = true;
    fetchFpV2Spec()
      .then((res) => {
        if (alive) setSpec(res);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const loadUniverse = useCallback(async () => {
    setUniLoading(true);
    setUniError(null);
    try {
      const res = await fetchFpV2Universe({ limit: 18, horizonDays, stableBand });
      setUniData(res);
    } catch (err) {
      setUniError(err?.message || "Gagal memuat perbandingan universe v1 vs v2.");
    } finally {
      setUniLoading(false);
    }
  }, [horizonDays, stableBand]);

  useEffect(() => {
    if (activeTab === "universe") loadUniverse();
  }, [activeTab, loadUniverse]);

  const uniSymbols = useMemo(
    () => (uniData?.data || []).map((r) => r.symbol),
    [uniData]
  );
  const { ticks: liveTicks } = useLiveTickers(uniSymbols);

  const loadAsset = useCallback(
    async (sym) => {
      setAssetLoading(true);
      setAssetError(null);
      try {
        const res = await fetchFpV2Asset(String(sym || "BTCUSDT").toUpperCase(), {
          horizonDays,
          stableBand,
          microHorizonSec: 30,
          microBand: 0.004,
        });
        setAssetData(res);
      } catch (err) {
        setAssetError(err?.message || `Gagal memuat perbandingan ${sym}.`);
      } finally {
        setAssetLoading(false);
      }
    },
    [horizonDays, stableBand]
  );

  useEffect(() => {
    if (activeTab === "asset") loadAsset(selectedSymbol);
  }, [activeTab, selectedSymbol, loadAsset]);

  // Debounced dual simulation (v1 & v2 run on exact same input)
  useEffect(() => {
    if (activeTab !== "spoofing") return undefined;
    let alive = true;
    const timer = setTimeout(async () => {
      setSimBusy(true);
      setSimError(null);
      try {
        const res = await simulateFpV2(cleanSimPayload(obParams));
        if (alive) setSimData(res);
      } catch (err) {
        if (alive) setSimError(err?.message || "Simulasi perbandingan gagal.");
      } finally {
        if (alive) setSimBusy(false);
      }
    }, 140);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [activeTab, obParams]);

  // Live Binance WS feeding identical packets into both v1 and v2
  useEffect(() => {
    if (!wsEnabled || activeTab !== "spoofing") {
      if (wsUnsubRef.current) {
        wsUnsubRef.current();
        wsUnsubRef.current = null;
      }
      if (!wsEnabled) setWsStatus("idle");
      return undefined;
    }

    setWsMessage(null);
    const unsub = subscribeOrderbookStreamV2(
      selectedSymbol,
      (snap) => {
        setActivePresetId("live_ws");
        setSnapMeta({
          live: true,
          source: "binance-ws-live",
          symbol: snap.symbol,
          midPrice: snap.midPrice,
          spreadBps: snap.spreadBps,
          note: `Stream identik ke v1 (top-20 mentah) & v2 (3 pilar, λ=${Math.round(
            snap.decayLambda
          )}) · ${snap.samples} trade/15dtk.`,
        });
        setObParams((prev) => ({
          ...prev,
          bidDepth: Number(snap.bidDepth.toFixed(4)),
          askDepth: Number(snap.askDepth.toFixed(4)),
          farBidDepth: Number((snap.farBidDepth || 0).toFixed(4)),
          farAskDepth: Number((snap.farAskDepth || 0).toFixed(4)),
          rawBidForV1: Number(snap.rawBidForV1.toFixed(4)),
          rawAskForV1: Number(snap.rawAskForV1.toFixed(4)),
          takerBuyVol: Number(snap.takerBuyVol.toFixed(4)),
          takerSellVol: Number(snap.takerSellVol.toFixed(4)),
          canceledBidVol: Number(snap.canceledBidVol.toFixed(4)),
          canceledAskVol: Number(snap.canceledAskVol.toFixed(4)),
          rawCancelBidForV1: Number(snap.rawCancelBidForV1.toFixed(4)),
          rawCancelAskForV1: Number(snap.rawCancelAskForV1.toFixed(4)),
          spreadBps: Number(Math.max(0.1, snap.spreadBps || 1).toFixed(3)),
          priceChangeBps:
            snap.priceChangeBps != null ? Number(snap.priceChangeBps.toFixed(2)) : null,
          tradeWindowSec: Number(Math.max(1, snap.tradeWindowSec || 15).toFixed(1)),
          bestBid: snap.bestBid,
          bestAsk: snap.bestAsk,
          bestBidQty: snap.bestBidQty,
          bestAskQty: snap.bestAskQty,
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
  }, [wsEnabled, activeTab, selectedSymbol]);

  const handlePullSnapshot = async () => {
    setWsEnabled(false);
    setSnapBusy(true);
    setSimError(null);
    try {
      const res = await fetchFpV2Asset(selectedSymbol, {
        horizonDays,
        stableBand,
        microHorizonSec: obParams.horizonSec || 30,
        microBand: obParams.stableBand || 0.004,
      });
      const mc = res?.microCompare;
      const inp = mc?.inputs;
      if (inp) {
        setActivePresetId("api_snapshot");
        setObParams((prev) => ({
          ...prev,
          bidDepth: inp.bidDepth,
          askDepth: inp.askDepth,
          farBidDepth: inp.farBidDepth || 0,
          farAskDepth: inp.farAskDepth || 0,
          rawBidForV1: inp.rawBidForV1 ?? null,
          rawAskForV1: inp.rawAskForV1 ?? null,
          takerBuyVol: inp.takerBuyVol,
          takerSellVol: inp.takerSellVol,
          canceledBidVol: inp.canceledBidVol,
          canceledAskVol: inp.canceledAskVol,
          rawCancelBidForV1: inp.rawCancelBidForV1 ?? null,
          rawCancelAskForV1: inp.rawCancelAskForV1 ?? null,
          spreadBps: inp.spreadBps != null ? Math.max(0.1, inp.spreadBps) : 8,
          priceChangeBps: inp.priceChangeBps ?? null,
          tradeWindowSec: inp.tradeWindowSec ?? 15,
          bestBid: inp.bestBid ?? null,
          bestAsk: inp.bestAsk ?? null,
          bestBidQty: inp.bestBidQty ?? null,
          bestAskQty: inp.bestAskQty ?? null,
          sigmaPerSec: inp.sigmaPerSec,
          midPrice: inp.midPrice ?? null,
        }));
        setSnapMeta({
          live: Boolean(mc.live),
          source: mc.source,
          symbol: res.symbol,
          midPrice: inp.midPrice,
          spreadBps: inp.spreadBps,
          note:
            mc.warning ||
            `Snapshot identik diuji ke v1 (top-20 mentah) & v2 (${inp.levelsUsed || 100} level, λ=${Math.round(
              inp.decayLambda || 550
            )}).`,
        });
      }
    } catch (err) {
      setSimError(err?.message || "Gagal mengambil snapshot API.");
    } finally {
      setSnapBusy(false);
    }
  };

  const handleRunBacktest = async () => {
    setBtBusy(true);
    setBtError(null);
    try {
      const res = await runFpV2Backtest(btParams);
      setBtData(res);
    } catch (err) {
      setBtError(err?.message || "Gagal menjalankan adu akurasi walk-forward.");
    } finally {
      setBtBusy(false);
    }
  };

  const openAsset = (sym) => {
    const clean = String(sym || "").toUpperCase();
    setSelectedSymbol(clean);
    setActiveTab("asset");
    navigate(`/fokker-planck-v2/${clean}`, { replace: false });
  };

  const handleCustomSubmit = (e) => {
    e.preventDefault();
    const raw = customSymInput.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!raw) return;
    setCustomSymInput("");
    openAsset(raw.endsWith("USDT") ? raw : `${raw}USDT`);
  };

  const manualUpdateOb = (patch) => {
    setActivePresetId("custom");
    setObParams((p) => ({
      ...p,
      // Clear live-only raw overrides so both v1 & v2 respond directly to slider values
      rawBidForV1: null,
      rawAskForV1: null,
      rawCancelBidForV1: null,
      rawCancelAskForV1: null,
      bestBid: null,
      bestAsk: null,
      bestBidQty: null,
      bestAskQty: null,
      ...patch,
    }));
  };

  const filteredRows = useMemo(() => {
    const q = searchQuery.trim().toUpperCase();
    return (uniData?.data || []).filter((r) => {
      if (q && !r.symbol.includes(q) && !r.baseAsset.includes(q)) return false;
      if (consensusFilter === "STRONG") return r.comparison.status === "SEPAKAT_KUAT";
      if (consensusFilter === "SAME_DIR") return r.comparison.sameDirection;
      if (consensusFilter === "DIFF") {
        return !r.comparison.sameRegime || r.comparison.status === "FILTER_V2_AKTIF";
      }
      return true;
    });
  }, [uniData, searchQuery, consensusFilter]);

  const presets = spec?.presets?.length ? spec.presets : FALLBACK_PRESETS;
  const { obMaxScale, obStep, obDigits } = useMemo(() => {
    const peak = Math.max(
      obParams.bidDepth || 0,
      obParams.askDepth || 0,
      obParams.farBidDepth || 0,
      obParams.farAskDepth || 0,
      obParams.takerBuyVol || 0,
      obParams.takerSellVol || 0,
      obParams.canceledBidVol || 0,
      obParams.canceledAskVol || 0,
      1
    );
    const maxVal = Math.max(0.1, Number((peak * 1.5).toPrecision(2)));
    const stepVal = Math.max(0.001, Number((maxVal / 200).toPrecision(2)));
    const digits = maxVal < 20 ? 2 : maxVal < 2000 ? 1 : 0;
    return { obMaxScale: maxVal, obStep: stepVal, obDigits: digits };
  }, [
    obParams.bidDepth,
    obParams.askDepth,
    obParams.farBidDepth,
    obParams.farAskDepth,
    obParams.takerBuyVol,
    obParams.takerSellVol,
    obParams.canceledBidVol,
    obParams.canceledAskVol,
  ]);

  const candleCmp = assetData?.candleCompare;
  const microCmp = assetData?.microCompare;

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-8 md:py-10">
      {/* Header */}
      <div className="border-b border-line pb-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="micro-label text-gold-600">
            Model Terpisah · Perbandingan Berdampingan v1 (Asli) vs v2 (3 Pilar Anti-Spoof)
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={activeTab === "asset" ? `/fp-live/${selectedSymbol}` : "/fp-live"}
              className="btn-ghost !py-1.5 !px-3 text-[11.5px]"
            >
              Buka FP Live 5–30m
              <ArrowUpRight size={13} />
            </Link>
            <Link
              to={activeTab === "asset" ? `/fokker-planck/${selectedSymbol}` : "/fokker-planck"}
              className="btn-ghost !py-1.5 !px-3 text-[11.5px]"
            >
              Buka v1 Asli (Utuh)
              <ArrowUpRight size={13} />
            </Link>
          </div>
        </div>

        <h1 className="mt-2 font-display text-3xl sm:text-4xl font-semibold tracking-tight text-ink">
          Fokker–Planck v2 & Uji Kesamaan v1 vs v2
        </h1>
        <p className="mt-2 text-[14px] leading-relaxed text-muted max-w-3xl">
          Sistem & tampilan <strong>Model v1</strong> dibiarkan 100% utuh seperti semula. Halaman
          terpisah ini menjalankan <strong>Model v1</strong> dan <strong>Model v2</strong>{" "}
          (Prioritas Taker + Iceberg Guard, Stoikov Micro-Price, dan Exponential Decay{" "}
          <code className="font-mono text-[12px]">e^(−λd)</code>) secara berdampingan pada data
          yang sama untuk mencari titik sepakat (konfirmasi sinyal) dan membandingkan akurasinya.
        </p>

        {/* Sub-navigation Tabs */}
        <div className="mt-5 flex flex-wrap gap-2">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = activeTab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setActiveTab(t.id)}
                className={`focus-ring inline-flex items-center gap-2 rounded-full px-4 py-2 text-[12.5px] font-medium border transition-colors ${
                  active
                    ? "bg-pine-800 text-paper border-pine-800"
                    : "bg-card text-muted border-line hover:text-ink hover:border-pine-300"
                }`}
              >
                <Icon size={14} />
                {t.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Shared Horizon & Band bar for Tabs 1 & 2 */}
      {(activeTab === "universe" || activeTab === "asset") && (
        <div className="mt-6 card p-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="micro-label">Horizon:</span>
              <div className="inline-flex rounded-full border border-lineStrong bg-paper p-0.5">
                {HORIZON_OPTIONS.map((o) => (
                  <button
                    key={o.value}
                    type="button"
                    onClick={() => setHorizonDays(o.value)}
                    className={`rounded-full px-3 py-1 font-mono text-[11px] transition-colors ${
                      horizonDays === o.value
                        ? "bg-pine-800 text-paper"
                        : "text-muted hover:text-ink"
                    }`}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex items-center gap-2">
              <span className="micro-label">Zona Stabil:</span>
              <div className="inline-flex rounded-full border border-lineStrong bg-paper p-0.5">
                {BAND_OPTIONS.map((o) => (
                  <button
                    key={o.value}
                    type="button"
                    onClick={() => setStableBand(o.value)}
                    className={`rounded-full px-2.5 py-1 font-mono text-[11px] transition-colors ${
                      stableBand === o.value
                        ? "bg-pine-800 text-paper"
                        : "text-muted hover:text-ink"
                    }`}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <button
            type="button"
            onClick={() => (activeTab === "universe" ? loadUniverse() : loadAsset(selectedSymbol))}
            className="btn-ghost !py-1.5 !px-3.5 text-[12px]"
          >
            <RefreshCw size={13} className={uniLoading || assetLoading ? "animate-spin" : ""} />
            Muat Ulang
          </button>
        </div>
      )}

      {/* ================================================================== */}
      {/* TAB 1 — KONSENSUS UNIVERSE (v1 vs v2 BERDAMPINGAN)                 */}
      {/* ================================================================== */}
      {activeTab === "universe" && (
        <section className="mt-6 space-y-6 animate-fade-up">
          {uniError && (
            <div className="card p-4 border-[#E3B7AC] bg-[#F6E4E0] text-[#8A3324] text-sm">
              {uniError}
            </div>
          )}

          {uniData?.summary && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <StatTile
                label="Koin Sepakat Rezim (v1 = v2)"
                value={`${uniData.summary.sameRegimeCount} / ${uniData.summary.universe}`}
                sub={`Tingkat kesepakatan ${fmtPct(uniData.summary.agreementRate, { digits: 0 })}`}
                tone="pine"
              />
              <StatTile
                label="Rata-rata Overlap Kurva"
                value={fmtPct(uniData.summary.avgOverlap, { digits: 1 })}
                sub={`${uniData.summary.strongConsensusCount} koin Sepakat Kuat (≥85%)`}
                tone="pine"
              />
              <StatTile
                label="Rata-rata P(Stabil) v1 vs v2"
                value={`${fmtPct(uniData.summary.avgPStableV1, { digits: 1 })} · ${fmtPct(
                  uniData.summary.avgPStableV2,
                  { digits: 1 }
                )}`}
                sub="Kiri: v1 Asli · Kanan: v2 Baru"
                tone="gold"
              />
              <StatTile
                label="Filter v2 / Beda Bacaan"
                value={String(uniData.summary.divergenceCount)}
                sub={`Sumber: ${uniData.source || "auto"}`}
                tone="ink"
              />
            </div>
          )}

          {/* Filter + Search */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div className="flex flex-wrap gap-1.5">
              {CONSENSUS_FILTERS.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setConsensusFilter(f.id)}
                  className={`rounded-full px-3.5 py-1.5 text-[12px] font-medium border transition-colors ${
                    consensusFilter === f.id
                      ? "bg-pine-800 text-paper border-pine-800"
                      : "bg-card text-muted border-line hover:text-ink"
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>

            <div className="relative w-full sm:w-60">
              <Search size={14} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-faint" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Cari simbol (BTC, SOL…)"
                className="w-full rounded-full border border-lineStrong bg-card pl-9 pr-4 py-1.5 text-[12.5px] text-ink placeholder:text-faint"
              />
            </div>
          </div>

          {/* Side-by-side table */}
          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Aset</th>
                    <th>Harga</th>
                    <th>Model v1 (Asli)</th>
                    <th>P(Stabil) v1</th>
                    <th>Model v2 (3 Pilar)</th>
                    <th>P(Stabil) v2</th>
                    <th>Δ (v2−v1)</th>
                    <th>Kesamaan</th>
                    <th>Konsensus</th>
                  </tr>
                </thead>
                <tbody>
                  {uniLoading && !uniData ? (
                    <tr>
                      <td colSpan={9} className="py-10 text-center text-sm text-muted">
                        Menghitung distribusi v1 dan v2 secara berdampingan…
                      </td>
                    </tr>
                  ) : (
                    filteredRows.map((row) => {
                      const px = liveTicks[row.symbol]?.price ?? row.price;
                      const dp = row.comparison.deltaPStable * 100;
                      return (
                        <tr
                          key={row.symbol}
                          className="row-link"
                          onClick={() => openAsset(row.symbol)}
                        >
                          <td>
                            <span className="font-mono text-[13px] font-semibold text-ink">
                              {row.baseAsset}
                            </span>
                            <span className="font-mono text-[10.5px] text-faint ml-1">/USDT</span>
                          </td>
                          <td className="font-mono text-[12px] text-ink tabular-nums">
                            ${fmtPrice(px)}
                          </td>
                          <td>
                            <VerdictBadge tone={row.v1.regimeTone} label={row.v1.regimeLabel} />
                          </td>
                          <td className="font-mono text-[12.5px] text-ink tabular-nums">
                            {fmtPct(row.v1.metrics.pStable, { digits: 1 })}
                          </td>
                          <td>
                            <VerdictBadge tone={row.v2.regimeTone} label={row.v2.regimeLabel} />
                          </td>
                          <td className="font-mono text-[12.5px] font-semibold text-pine-800 tabular-nums">
                            {fmtPct(row.v2.metrics.pStable, { digits: 1 })}
                          </td>
                          <td
                            className={`font-mono text-[12px] tabular-nums ${
                              dp > 0.5 ? "text-up" : dp < -0.5 ? "text-down" : "text-muted"
                            }`}
                          >
                            {fmtSigned(dp, 1, " pp")}
                          </td>
                          <td className="font-mono text-[12px] font-semibold text-ink tabular-nums">
                            {fmtPct(row.comparison.pdfOverlap, { digits: 1 })}
                          </td>
                          <td>
                            <VerdictBadge
                              tone={row.comparison.tone}
                              label={row.comparison.label}
                            />
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}

      {/* ================================================================== */}
      {/* TAB 2 — DISTRIBUSI SATU ASET: v1 (KIRI) vs v2 (KANAN) + OVERLAY    */}
      {/* ================================================================== */}
      {activeTab === "asset" && (
        <section className="mt-6 space-y-6 animate-fade-up">
          {/* Symbol selector */}
          <div className="card p-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="micro-label">Pilih Aset:</span>
              <select
                value={selectedSymbol}
                onChange={(e) => openAsset(e.target.value)}
                className="rounded-full border border-lineStrong bg-paper px-3.5 py-1.5 font-mono text-[12.5px] font-semibold text-ink"
              >
                {Array.from(
                  new Set([selectedSymbol, ...(uniData?.data || []).map((r) => r.symbol)])
                ).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => {
                  setActiveTab("spoofing");
                  handlePullSnapshot();
                }}
                className="btn-ghost !py-1.5 !px-3 text-[12px]"
              >
                Uji Order-Book {selectedSymbol} →
              </button>
            </div>

            <form onSubmit={handleCustomSubmit} className="flex items-center gap-2">
              <input
                type="text"
                value={customSymInput}
                onChange={(e) => setCustomSymInput(e.target.value)}
                placeholder="Ketik simbol lain (mis. PEPE)"
                className="rounded-full border border-lineStrong bg-paper px-3.5 py-1.5 text-[12px] text-ink placeholder:text-faint w-48"
              />
              <button type="submit" className="btn-primary !py-1.5 !px-3.5 text-[12px]">
                Bandingkan
              </button>
            </form>
          </div>

          {assetError && (
            <div className="card p-4 border-[#E3B7AC] bg-[#F6E4E0] text-[#8A3324] text-sm">
              {assetError}
            </div>
          )}

          {assetLoading && !candleCmp && (
            <div className="card p-8 text-center text-sm text-muted">
              Menjalankan solver Fokker–Planck v1 dan v2 untuk {selectedSymbol}…
            </div>
          )}

          {candleCmp?.ok && (
            <>
              <ConsensusBanner cmp={candleCmp.comparison} />

              {/* Overlay PDF Chart */}
              <div className="card p-5 sm:p-6">
                <div className="flex flex-wrap items-baseline justify-between gap-2 mb-3">
                  <div>
                    <p className="micro-label">Overlay Kurva Distribusi (Data Identik)</p>
                    <h2 className="font-display text-xl font-semibold text-ink mt-0.5">
                      {assetData.symbol} · P_v1(x,T) vs P_v2(x,T) ({candleCmp.v1.horizonDays} Hari,
                      Zona ±{(candleCmp.v1.stableBand * 100).toFixed(0)}%)
                    </h2>
                  </div>
                  <span className="font-mono text-[11.5px] text-muted tabular-nums">
                    Harga ${fmtPrice(candleCmp.v1.price)} · sumber {assetData.source}
                  </span>
                </div>
                <OverlayPdfChart
                  overlayCurve={candleCmp.overlayCurve}
                  stableBand={candleCmp.v1.stableBand}
                  unitMode="pct"
                  height={295}
                />
              </div>

              {/* Side-by-Side v1 (Left) vs v2 (Right) */}
              <div className="grid lg:grid-cols-2 gap-6">
                {/* LEFT: Untouched Model v1 */}
                <div className="card p-5 sm:p-6 space-y-4 border-t-4 border-t-gold-500">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <span className="micro-label text-gold-700">
                        Model v1 · Baseline Asli (Tidak Diubah)
                      </span>
                      <h3 className="font-display text-xl font-semibold text-ink mt-1">
                        {candleCmp.v1.regimeLabel}
                      </h3>
                    </div>
                    <VerdictBadge
                      tone={candleCmp.v1.regimeTone}
                      label={candleCmp.v1.regimeLabel}
                    />
                  </div>

                  <ZoneBar m={candleCmp.v1.metrics} />

                  <dl className="divide-y divide-line text-[12.5px]">
                    {[
                      ["P(Stabil)", fmtPct(candleCmp.v1.metrics.pStable, { digits: 2 })],
                      ["P(Downside) / P(Upside)", `${fmtPct(candleCmp.v1.metrics.pDownside, { digits: 1 })} / ${fmtPct(candleCmp.v1.metrics.pUpside, { digits: 1 })}`],
                      ["P(Crash Ekor <-2a)", fmtPct(candleCmp.v1.metrics.pTailCrash, { digits: 2 })],
                      ["Ekspektasi Return E[r]", fmtSigned(candleCmp.v1.metrics.expectedReturnPct, 2, "%")],
                      ["Drift Aliran μ (Flat 30h)", fmtSigned(candleCmp.v1.parameters.muFlowDailyPct, 3, "%/h")],
                      ["Volatilitas Harian √D₀", `${fmtNum(candleCmp.v1.parameters.sigmaDailyPct, 2)}%`],
                      ["Taker-Buy Share (Rata)", fmtPct(candleCmp.v1.parameters.buyShare30d, { digits: 1 })],
                      ["Jangkar VWAP-20 (Rata)", `$${fmtPrice(candleCmp.v1.vwap20)} (${fmtSigned(candleCmp.v1.parameters.vwapAnchorPct, 2, "%")})`],
                    ].map(([k, v]) => (
                      <div key={k} className="py-2 flex justify-between gap-3">
                        <dt className="text-muted">{k}</dt>
                        <dd className="font-mono font-medium text-ink tabular-nums">{v}</dd>
                      </div>
                    ))}
                  </dl>
                </div>

                {/* RIGHT: 3-Pillar Model v2 */}
                <div className="card p-5 sm:p-6 space-y-4 border-t-4 border-t-pine-700">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <span className="micro-label text-pine-700">
                        Model v2 · Terjemahan 3 Pilar Baru
                      </span>
                      <h3 className="font-display text-xl font-semibold text-ink mt-1">
                        {candleCmp.v2.regimeLabel}
                      </h3>
                    </div>
                    <VerdictBadge
                      tone={candleCmp.v2.regimeTone}
                      label={candleCmp.v2.regimeLabel}
                    />
                  </div>

                  <ZoneBar m={candleCmp.v2.metrics} />

                  <dl className="divide-y divide-line text-[12.5px]">
                    {[
                      ["P(Stabil)", fmtPct(candleCmp.v2.metrics.pStable, { digits: 2 })],
                      ["P(Downside) / P(Upside)", `${fmtPct(candleCmp.v2.metrics.pDownside, { digits: 1 })} / ${fmtPct(candleCmp.v2.metrics.pUpside, { digits: 1 })}`],
                      ["P(Crash Ekor <-2a)", fmtPct(candleCmp.v2.metrics.pTailCrash, { digits: 2 })],
                      ["Ekspektasi Return E[r]", fmtSigned(candleCmp.v2.metrics.expectedReturnPct, 2, "%")],
                      ["Drift Taker + Iceberg Guard", fmtSigned(candleCmp.v2.parameters.muFlowDailyPct, 3, "%/h")],
                      ["Volatilitas √D₀ (Exp-Decay)", `${fmtNum(candleCmp.v2.parameters.sigmaDailyPct, 2)}%`],
                      [
                        "Taker Share (Exp) & Absorpsi",
                        `${fmtPct(candleCmp.v2.parameters.buyShare30d, { digits: 1 })} · abs ${Math.round(
                          (candleCmp.v2.parameters.absorptionScore || 0) * 100
                        )}%`,
                      ],
                      [
                        "Jangkar Micro + e^(−λd) VWAP",
                        `$${fmtPrice(candleCmp.v2.microAnchorPrice)} (${fmtSigned(
                          candleCmp.v2.parameters.vwapAnchorPct,
                          2,
                          "%"
                        )}, λ=${candleCmp.v2.parameters.decayLambdaCandle})`,
                      ],
                    ].map(([k, v]) => (
                      <div key={k} className="py-2 flex justify-between gap-3">
                        <dt className="text-muted">{k}</dt>
                        <dd className="font-mono font-medium text-ink tabular-nums">{v}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </div>

              {/* Quick Order-book snapshot comparison preview */}
              {microCmp?.ok && (
                <div className="card p-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                  <div>
                    <span className="micro-label">
                      Ringkasan Mikrostruktur Order-Book ({microCmp.source})
                    </span>
                    <p className="mt-1 text-[13px] text-ink">
                      <strong>v1:</strong> {microCmp.v1.regimeLabel} (P_stabil{" "}
                      {fmtPct(microCmp.v1.metrics.pStable, { digits: 1 })}) ·{" "}
                      <strong>v2:</strong> {microCmp.v2.regimeLabel} (P_stabil{" "}
                      {fmtPct(microCmp.v2.metrics.pStable, { digits: 1 })}) ·{" "}
                      <strong>Overlap {fmtPct(microCmp.comparison.pdfOverlap, { digits: 1 })}</strong>
                    </p>
                  </div>
                  <VerdictBadge
                    tone={microCmp.comparison.tone}
                    label={microCmp.comparison.label}
                  />
                </div>
              )}
            </>
          )}
        </section>
      )}

      {/* ================================================================== */}
      {/* TAB 3 — LAB ORDER-BOOK & SPOOFING: v1 (KIRI) vs v2 (KANAN)         */}
      {/* ================================================================== */}
      {activeTab === "spoofing" && (
        <section className="mt-6 space-y-6 animate-fade-up">
          {/* Toolbar */}
          <div className="card p-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <span className="micro-label text-pine-700">Input Tunggal → Dua Model Sekaligus</span>
              <p className="text-[12.5px] text-muted mt-0.5">
                v1 menerima buku order mentah tanpa redaman jarak; v2 menerjemahkannya lewat
                Prioritas Taker + Iceberg, Micro-Price, & <code className="font-mono">e^(−λd)</code>.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <select
                value={selectedSymbol}
                onChange={(e) => setSelectedSymbol(e.target.value)}
                className="rounded-full border border-lineStrong bg-paper px-3.5 py-2 font-mono text-[12px] font-semibold text-ink"
              >
                {Array.from(
                  new Set([selectedSymbol, ...(uniData?.data || []).map((r) => r.symbol)])
                ).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>

              <button
                type="button"
                onClick={handlePullSnapshot}
                disabled={snapBusy}
                className="btn-ghost !py-2 !px-3.5 text-[12px]"
              >
                <RefreshCw size={13} className={snapBusy ? "animate-spin" : ""} />
                Snapshot API
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
                <Radio size={13} className={wsEnabled ? "animate-pulse" : ""} />
                {wsEnabled
                  ? wsStatus === "live"
                    ? "Stop Live WS"
                    : "Menghubungkan…"
                  : "Live WS v1 vs v2"}
              </button>
            </div>
          </div>

          {wsMessage && (
            <div className="flex items-start gap-2.5 rounded-xl border border-gold-300 bg-gold-100/70 px-4 py-3 text-[12.5px] text-gold-700">
              <WifiOff size={15} className="mt-0.5 shrink-0" />
              <span>{wsMessage}</span>
            </div>
          )}

          {snapMeta && (activePresetId === "api_snapshot" || activePresetId === "live_ws") && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-pine-200 bg-pine-50 px-4 py-2.5 text-[12px] text-pine-800">
              <div className="flex items-center gap-2">
                <Info size={14} className="shrink-0 text-pine-600" />
                <span>
                  <strong>{snapMeta.symbol}</strong> ({snapMeta.source})
                  {snapMeta.midPrice ? ` · Mid $${fmtPrice(snapMeta.midPrice)}` : ""}
                  {snapMeta.note ? ` — ${snapMeta.note}` : ""}
                </span>
              </div>
              <span className="font-mono text-[10px] uppercase tracking-micro">
                {snapMeta.live ? "LIVE" : "PROXY CANDLE"}
              </span>
            </div>
          )}

          {/* 8 Comparison Presets */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
            {presets.map((pr) => {
              const active = activePresetId === pr.id;
              return (
                <button
                  key={pr.id}
                  type="button"
                  onClick={() => {
                    setWsEnabled(false);
                    setActivePresetId(pr.id);
                    setObParams({
                      farBidDepth: 0,
                      farAskDepth: 0,
                      wallDistancePct: 0.008,
                      spreadBps: 8,
                      priceChangeBps: null,
                      ...pr.params,
                      rawBidForV1: null,
                      rawAskForV1: null,
                      rawCancelBidForV1: null,
                      rawCancelAskForV1: null,
                      bestBid: null,
                      bestAsk: null,
                      bestBidQty: null,
                      bestAskQty: null,
                      midPrice: null,
                    });
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

          {simError && (
            <div className="card p-4 border-[#E3B7AC] bg-[#F6E4E0] text-[#8A3324] text-sm">
              {simError}
            </div>
          )}

          <div className="grid lg:grid-cols-12 gap-6">
            {/* Shared Inputs Column */}
            <div className="card p-5 lg:col-span-4 space-y-4">
              <div className="flex items-center justify-between">
                <p className="micro-label">Input Pasar Bersama (v1 & v2)</p>
                {simBusy && (
                  <span className="font-mono text-[10px] text-muted">Menghitung…</span>
                )}
              </div>

              <div className="space-y-3">
                <p className="font-mono text-[10px] uppercase tracking-micro text-pine-700 font-semibold">
                  01 · Transaksi Nyata (Taker) & Dampak Harga
                </p>
                <SliderField
                  label="Taker Buy (Market Buy)"
                  value={obParams.takerBuyVol}
                  min={0}
                  max={obMaxScale}
                  step={obStep}
                  display={fmtNum(obParams.takerBuyVol, obDigits)}
                  onChange={(v) => manualUpdateOb({ takerBuyVol: v })}
                />
                <SliderField
                  label="Taker Sell (Market Sell)"
                  value={obParams.takerSellVol}
                  min={0}
                  max={obMaxScale}
                  step={obStep}
                  display={fmtNum(obParams.takerSellVol, obDigits)}
                  onChange={(v) => manualUpdateOb({ takerSellVol: v })}
                />
                <div className="rounded-lg border border-line bg-paper/70 p-2.5 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11.5px] font-medium text-ink">
                      Sertakan Δ Harga Aktual (Uji Iceberg)
                    </span>
                    <input
                      type="checkbox"
                      checked={obParams.priceChangeBps !== null && obParams.priceChangeBps !== undefined}
                      onChange={(e) =>
                        manualUpdateOb({ priceChangeBps: e.target.checked ? 0 : null })
                      }
                      className="accent-pine-700 cursor-pointer"
                    />
                  </div>
                  {obParams.priceChangeBps !== null && obParams.priceChangeBps !== undefined && (
                    <SliderField
                      label="Perubahan Harga (Δp)"
                      value={obParams.priceChangeBps}
                      min={-40}
                      max={40}
                      step={1}
                      display={fmtSigned(obParams.priceChangeBps, 1, " bps")}
                      onChange={(v) => manualUpdateOb({ priceChangeBps: v })}
                    />
                  )}
                </div>
              </div>

              <div className="pt-3 border-t border-line space-y-3">
                <p className="font-mono text-[10px] uppercase tracking-micro text-gold-700 font-semibold">
                  02 · Buku Order Dekat, Spread & Cancel
                </p>
                <SliderField
                  label="Bid Dekat Mid"
                  value={obParams.bidDepth}
                  min={obStep}
                  max={obMaxScale}
                  step={obStep}
                  display={fmtNum(obParams.bidDepth, obDigits)}
                  onChange={(v) => manualUpdateOb({ bidDepth: v })}
                />
                <SliderField
                  label="Ask Dekat Mid"
                  value={obParams.askDepth}
                  min={obStep}
                  max={obMaxScale}
                  step={obStep}
                  display={fmtNum(obParams.askDepth, obDigits)}
                  onChange={(v) => manualUpdateOb({ askDepth: v })}
                />
                <SliderField
                  label="Spread Bid–Ask"
                  value={obParams.spreadBps ?? 8}
                  min={0.5}
                  max={50}
                  step={0.5}
                  display={`${fmtNum(obParams.spreadBps ?? 8, 1)} bps`}
                  onChange={(v) => manualUpdateOb({ spreadBps: v })}
                />
                <SliderField
                  label="Cancel Bid"
                  value={obParams.canceledBidVol}
                  min={0}
                  max={obMaxScale}
                  step={obStep}
                  display={fmtNum(obParams.canceledBidVol, obDigits)}
                  onChange={(v) => manualUpdateOb({ canceledBidVol: v })}
                />
                <SliderField
                  label="Cancel Ask"
                  value={obParams.canceledAskVol}
                  min={0}
                  max={obMaxScale}
                  step={obStep}
                  display={fmtNum(obParams.canceledAskVol, obDigits)}
                  onChange={(v) => manualUpdateOb({ canceledAskVol: v })}
                />
              </div>

              <div className="pt-3 border-t border-line space-y-3">
                <p className="font-mono text-[10px] uppercase tracking-micro text-muted font-semibold">
                  03 · Tembok Jauh (≥0,5% dari Mid)
                </p>
                <SliderField
                  label="Tembok Bid Jauh"
                  value={obParams.farBidDepth ?? 0}
                  min={0}
                  max={obMaxScale}
                  step={obStep}
                  display={fmtNum(obParams.farBidDepth ?? 0, obDigits)}
                  onChange={(v) => manualUpdateOb({ farBidDepth: v })}
                />
                <SliderField
                  label="Tembok Ask Jauh"
                  value={obParams.farAskDepth ?? 0}
                  min={0}
                  max={obMaxScale}
                  step={obStep}
                  display={fmtNum(obParams.farAskDepth ?? 0, obDigits)}
                  onChange={(v) => manualUpdateOb({ farAskDepth: v })}
                />
                <SliderField
                  label="Jarak Tembok (d)"
                  value={obParams.wallDistancePct ?? 0.008}
                  min={0.004}
                  max={0.015}
                  step={0.0005}
                  display={`${((obParams.wallDistancePct ?? 0.008) * 100).toFixed(2)}%`}
                  onChange={(v) => manualUpdateOb({ wallDistancePct: v })}
                />
              </div>

              <div className="pt-3 border-t border-line space-y-3">
                <SliderField
                  label="Volatilitas σ / Detik"
                  value={obParams.sigmaPerSec}
                  min={0.0002}
                  max={0.0035}
                  step={0.0001}
                  display={`${(obParams.sigmaPerSec * 10000).toFixed(1)} bps/s`}
                  onChange={(v) => setObParams((p) => ({ ...p, sigmaPerSec: v }))}
                />
                <SliderField
                  label="Horizon (T)"
                  value={obParams.horizonSec}
                  min={5}
                  max={120}
                  step={5}
                  display={`${obParams.horizonSec} detik`}
                  onChange={(v) => setObParams((p) => ({ ...p, horizonSec: v }))}
                />
              </div>
            </div>

            {/* Side-by-Side v1 vs v2 Output Column */}
            <div className="lg:col-span-8 space-y-5">
              {simData?.ok && (
                <>
                  <ConsensusBanner cmp={simData.comparison} />

                  {/* LEFT = Model v1 · RIGHT = Model v2 */}
                  <div className="grid md:grid-cols-2 gap-4">
                    {/* v1 Card */}
                    <div className="card p-5 space-y-3.5 border-t-4 border-t-gold-500">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <span className="micro-label text-gold-700">Model v1 (Asli · Utuh)</span>
                          <p className="text-[11.5px] text-muted mt-0.5">
                            Buku bobot rata, tanpa Micro-Price / Iceberg
                          </p>
                        </div>
                        <VerdictBadge
                          tone={simData.v1.regimeTone}
                          label={simData.v1.regimeLabel}
                        />
                      </div>

                      <ZoneBar m={simData.v1.metrics} />

                      <dl className="divide-y divide-line text-[12px] font-mono tabular-nums">
                        <div className="py-1.5 flex justify-between">
                          <dt className="font-sans text-muted">OBI Buku (Mentah)</dt>
                          <dd className="text-ink font-semibold">
                            {fmtSigned(simData.v1.microstructure.obi, 2)}
                          </dd>
                        </div>
                        <div className="py-1.5 flex justify-between">
                          <dt className="font-sans text-muted">TFI (67% bobot μ)</dt>
                          <dd className="text-ink">
                            {fmtSigned(simData.v1.microstructure.tfi, 2)}
                          </dd>
                        </div>
                        <div className="py-1.5 flex justify-between">
                          <dt className="font-sans text-muted">Drift Riil μ_v1</dt>
                          <dd className="text-ink font-semibold">
                            {fmtSigned(simData.v1.microstructure.muRealBpsPerSec, 2, " bps/s")}
                          </dd>
                        </div>
                        <div className="py-1.5 flex justify-between">
                          <dt className="font-sans text-muted">Spoof Index / P(Crash)</dt>
                          <dd className="text-ink">
                            {Math.round(simData.v1.microstructure.spoofIndex * 100)}% /{" "}
                            {fmtPct(simData.v1.metrics.pTailCrash, { digits: 1 })}
                          </dd>
                        </div>
                        <div className="py-1.5 flex justify-between">
                          <dt className="font-sans text-muted">Harga Jangkar</dt>
                          <dd className="text-muted">Mid-Price (0.00 bps)</dd>
                        </div>
                      </dl>
                    </div>

                    {/* v2 Card */}
                    <div className="card p-5 space-y-3.5 border-t-4 border-t-pine-700">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <span className="micro-label text-pine-700">Model v2 (3 Pilar Baru)</span>
                          <p className="text-[11.5px] text-muted mt-0.5">
                            Taker Priority + Micro-Price + e^(−λd)
                          </p>
                        </div>
                        <VerdictBadge
                          tone={simData.v2.regimeTone}
                          label={simData.v2.regimeLabel}
                        />
                      </div>

                      <ZoneBar m={simData.v2.metrics} />

                      <dl className="divide-y divide-line text-[12px] font-mono tabular-nums">
                        <div className="py-1.5 flex justify-between">
                          <dt className="font-sans text-muted">
                            OBI e^(−λd) (λ={Math.round(simData.v2.microstructure.decayLambda)})
                          </dt>
                          <dd className="text-pine-800 font-semibold">
                            {fmtSigned(simData.v2.microstructure.obi, 2)}{" "}
                            <span className="text-faint font-normal">
                              (raw {fmtSigned(simData.v2.microstructure.obiRaw, 2)})
                            </span>
                          </dd>
                        </div>
                        <div className="py-1.5 flex justify-between">
                          <dt className="font-sans text-muted">
                            TFI ({Math.round(simData.v2.microstructure.takerPriorityShare * 100)}%
                            bobot μ)
                          </dt>
                          <dd className="text-ink">
                            {fmtSigned(simData.v2.microstructure.tfi, 2)}
                            {simData.v2.microstructure.absorptionIndex >= 0.35
                              ? ` · Iceberg ${Math.round(
                                  simData.v2.microstructure.absorptionIndex * 100
                                )}%`
                              : ""}
                          </dd>
                        </div>
                        <div className="py-1.5 flex justify-between">
                          <dt className="font-sans text-muted">Drift Riil μ_v2</dt>
                          <dd className="text-pine-800 font-semibold">
                            {fmtSigned(simData.v2.microstructure.muRealBpsPerSec, 2, " bps/s")}
                          </dd>
                        </div>
                        <div className="py-1.5 flex justify-between">
                          <dt className="font-sans text-muted">Bobot d=0,5% / d=1,0%</dt>
                          <dd className="text-ink">
                            {(simData.v2.microstructure.weightAt05Pct * 100).toFixed(1)}% /{" "}
                            {(simData.v2.microstructure.weightAt10Pct * 100).toFixed(2)}%
                          </dd>
                        </div>
                      </dl>

                      <div className="pt-1">
                        <MicroPriceGauge
                          ms={simData.v2.microstructure}
                          hasRealPrice={Boolean(simData.midPrice)}
                        />
                      </div>
                    </div>
                  </div>

                  {/* Overlay PDF Chart */}
                  <div className="card p-5 sm:p-6">
                    <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
                      <div>
                        <p className="micro-label">Perbandingan Visual Kurva Kepadatan</p>
                        <h3 className="font-display text-lg font-semibold text-ink mt-0.5">
                          Overlay P_v1(x,T) vs P_v2(x,T) pada t = {simData.horizonSec} Detik
                        </h3>
                      </div>
                      <span className="font-mono text-[11px] text-pine-700 font-semibold tabular-nums">
                        Irisan = {(simData.comparison.pdfOverlap * 100).toFixed(1)}%
                      </span>
                    </div>
                    <OverlayPdfChart
                      overlayCurve={simData.overlayCurve}
                      stableBand={simData.stableBand}
                      unitMode="micro"
                      height={255}
                    />
                  </div>
                </>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ================================================================== */}
      {/* TAB 4 — ADU AKURASI WALK-FORWARD (v1 vs v2 vs KONSENSUS)           */}
      {/* ================================================================== */}
      {activeTab === "backtest" && (
        <section className="mt-6 space-y-6 animate-fade-up">
          <div className="card p-5 sm:p-6">
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
              <div>
                <p className="micro-label text-gold-600">Head-to-Head Tanpa Lookahead</p>
                <h2 className="font-display text-2xl font-semibold text-ink mt-1">
                  Adu Akurasi Historis: Model v1 vs Model v2 vs Konsensus
                </h2>
                <p className="mt-1.5 text-[13.5px] text-muted max-w-2xl">
                  Menguji v1 (asli), v2 (terjemahan 3 pilar), dan gabungan 50/50 (v1+v2) pada
                  titik waktu historis yang persis sama. Makin kecil <strong>Brier Score</strong>{" "}
                  dan <strong>Log-Loss</strong>, makin akurat probabilitasnya.
                </p>
              </div>
              <button
                type="button"
                onClick={handleRunBacktest}
                disabled={btBusy}
                className="btn-primary self-start md:self-auto"
              >
                <Play size={14} />
                {btBusy ? "Menguji v1 vs v2…" : "Jalankan Adu Akurasi v1 vs v2"}
              </button>
            </div>

            <div className="mt-5 pt-5 border-t border-line grid grid-cols-2 sm:grid-cols-4 gap-4">
              <label className="block">
                <span className="micro-label block mb-1.5">Horizon</span>
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
                <span className="micro-label block mb-1.5">Zona Stabil</span>
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
                <span className="micro-label block mb-1.5">Langkah Uji</span>
                <select
                  value={btParams.stepDays}
                  onChange={(e) =>
                    setBtParams((p) => ({ ...p, stepDays: Number(e.target.value) }))
                  }
                  className="w-full rounded-xl border border-lineStrong bg-paper px-3 py-2 font-mono text-[12px] text-ink"
                >
                  <option value={5}>Tiap 5 hari</option>
                  <option value={7}>Tiap 7 hari</option>
                  <option value={14}>Tiap 14 hari</option>
                </select>
              </label>

              <label className="block">
                <span className="micro-label block mb-1.5">Histori</span>
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
            <div className="card p-4 border-[#E3B7AC] bg-[#F6E4E0] text-[#8A3324] text-sm">
              {btError}
            </div>
          )}

          {btData?.ok && (
            <div className="space-y-6">
              <div className="card p-5 border-pine-200 bg-pine-50 flex items-start gap-3">
                <CheckCircle2 size={20} className="text-up mt-0.5 shrink-0" />
                <div>
                  <p className="font-sans text-[14.5px] font-semibold text-ink">
                    {btData.headToHead.headline} · {btData.samples} titik uji (
                    {btData.params.symbolsEvaluated} koin · {btData.source})
                  </p>
                  <p className="mt-1 text-[13px] text-muted">
                    Kesepakatan rezim v1 & v2:{" "}
                    <strong>
                      {fmtPct(btData.headToHead.regimeAgreementRate, { digits: 1 })}
                    </strong>{" "}
                    ({btData.headToHead.agreedSamples} sampel sepakat, Brier saat sepakat{" "}
                    <strong className="font-mono">{fmtNum(btData.headToHead.agreedBrierEns, 4)}</strong>
                    ) · Skor per koin: <strong>v2 unggul {btData.headToHead.v2SymbolWins}</strong>,{" "}
                    <strong>v1 unggul {btData.headToHead.v1SymbolWins}</strong>, seri{" "}
                    {btData.headToHead.ties}.
                  </p>
                </div>
              </div>

              {/* Side-by-side Accuracy Scoreboard + 4-Zone Chart */}
              <div className="grid lg:grid-cols-2 gap-6">
                <div className="card p-5 sm:p-6">
                  <p className="micro-label">Papan Skor Akurasi</p>
                  <h3 className="font-display text-xl font-semibold text-ink mt-0.5 mb-4">
                    Model v1 vs Model v2 vs Konsensus (v1+v2)
                  </h3>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Metrik</th>
                        <th>v1 (Asli)</th>
                        <th>v2 (Baru)</th>
                        <th>Gabungan</th>
                      </tr>
                    </thead>
                    <tbody className="font-mono text-[12px] tabular-nums">
                      <tr>
                        <td className="font-sans text-ink">Brier P(Stabil) ↓</td>
                        <td>{fmtNum(btData.v1.scores.brierStable, 4)}</td>
                        <td className="font-semibold text-pine-800">
                          {fmtNum(btData.v2.scores.brierStable, 4)}
                        </td>
                        <td>{fmtNum(btData.ensemble.scores.brierStable, 4)}</td>
                      </tr>
                      <tr>
                        <td className="font-sans text-ink">Brier Skill Score ↑</td>
                        <td>{fmtPct(btData.v1.scores.brierSkillScore, { signed: true, digits: 1 })}</td>
                        <td className="font-semibold text-pine-800">
                          {fmtPct(btData.v2.scores.brierSkillScore, { signed: true, digits: 1 })}
                        </td>
                        <td>
                          {fmtPct(btData.ensemble.scores.brierSkillScore, {
                            signed: true,
                            digits: 1,
                          })}
                        </td>
                      </tr>
                      <tr>
                        <td className="font-sans text-ink">Brier Multi-Zona ↓</td>
                        <td>{fmtNum(btData.v1.scores.brierMultiZone, 4)}</td>
                        <td className="font-semibold text-pine-800">
                          {fmtNum(btData.v2.scores.brierMultiZone, 4)}
                        </td>
                        <td>{fmtNum(btData.ensemble.scores.brierMultiZone, 4)}</td>
                      </tr>
                      <tr>
                        <td className="font-sans text-ink">Log-Loss 3 Zona ↓</td>
                        <td>{fmtNum(btData.v1.scores.logLoss3Zone, 4)}</td>
                        <td className="font-semibold text-pine-800">
                          {fmtNum(btData.v2.scores.logLoss3Zone, 4)}
                        </td>
                        <td>{fmtNum(btData.ensemble.scores.logLoss3Zone, 4)}</td>
                      </tr>
                      <tr>
                        <td className="font-sans text-ink">Gap Kalibrasi ↓</td>
                        <td>{fmtNum(btData.v1.scores.calibrationGapStable * 100, 1)} pp</td>
                        <td className="font-semibold text-pine-800">
                          {fmtNum(btData.v2.scores.calibrationGapStable * 100, 1)} pp
                        </td>
                        <td>{fmtNum(btData.ensemble.scores.calibrationGapStable * 100, 1)} pp</td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                <div className="card p-5 sm:p-6">
                  <p className="micro-label">Kalibrasi Empiris 4 Zona</p>
                  <h3 className="font-display text-xl font-semibold text-ink mt-0.5 mb-4">
                    Prediksi v1 vs v2 vs Realisasi Aktual
                  </h3>
                  <div style={{ width: "100%", height: 245 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart
                        data={btData.coverageComparison.map((c) => ({
                          zone: c.zone,
                          v1: Number((c.v1 * 100).toFixed(1)),
                          v2: Number((c.v2 * 100).toFixed(1)),
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
                        <Tooltip formatter={(v) => `${v}%`} />
                        <Legend wrapperStyle={{ fontSize: "11px" }} />
                        <Bar dataKey="v1" name="Model v1" fill="#B18A44" radius={[3, 3, 0, 0]} />
                        <Bar dataKey="v2" name="Model v2" fill="#144338" radius={[3, 3, 0, 0]} />
                        <Bar dataKey="Aktual" name="Aktual" fill="#68716A" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </div>

              {/* Per-Symbol Winner Table */}
              <div className="card overflow-hidden">
                <div className="px-5 pt-5 pb-3 border-b border-line">
                  <p className="micro-label">Rincian Akurasi & Kesepakatan per Koin</p>
                  <h3 className="font-display text-lg font-semibold text-ink mt-0.5">
                    Brier Score v1 vs v2 (Klik Baris untuk Lihat Kurva Berdampingan)
                  </h3>
                </div>
                <div className="overflow-x-auto">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Simbol</th>
                        <th>Sampel</th>
                        <th>Aktual Stabil</th>
                        <th>Brier v1</th>
                        <th>Brier v2</th>
                        <th>Sepakat Rezim</th>
                        <th>Lebih Akurat</th>
                      </tr>
                    </thead>
                    <tbody>
                      {btData.perSymbol.map((ps) => (
                        <tr
                          key={ps.symbol}
                          className="row-link"
                          onClick={() => openAsset(ps.symbol)}
                        >
                          <td className="font-mono text-[12.5px] font-semibold text-ink">
                            {ps.symbol}
                          </td>
                          <td className="font-mono text-[12px] text-muted tabular-nums">
                            {ps.samples}
                          </td>
                          <td className="font-mono text-[12px] text-ink tabular-nums">
                            {fmtPct(ps.actualStable, { digits: 1 })}
                          </td>
                          <td className="font-mono text-[12px] text-ink tabular-nums">
                            {fmtNum(ps.v1Brier, 4)}
                          </td>
                          <td className="font-mono text-[12px] text-pine-800 font-semibold tabular-nums">
                            {fmtNum(ps.v2Brier, 4)}
                          </td>
                          <td className="font-mono text-[12px] text-muted tabular-nums">
                            {fmtPct(ps.agreementRate, { digits: 0 })}
                          </td>
                          <td>
                            <VerdictBadge
                              tone={
                                ps.winner === "v2"
                                  ? "long"
                                  : ps.winner === "v1"
                                  ? "watch"
                                  : "neutral"
                              }
                              label={ps.winner === "Seri" ? "Seri" : `Model ${ps.winner}`}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* Exact Translation Diff Table (v1 vs v2) */}
          <div className="card p-5 sm:p-6 space-y-4">
            <div className="flex items-center gap-2 text-pine-700">
              <ShieldCheck size={16} />
              <span className="micro-label text-pine-700">
                Pembeda Kode: Solver Sama, Hanya Terjemahan Parameter Berbeda
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Komponen</th>
                    <th>Model v1 (`fokker_planck.py` — Utuh)</th>
                    <th>Model v2 (`fokker_planck_v2.py` — Terpisah)</th>
                  </tr>
                </thead>
                <tbody className="text-[12.5px]">
                  {(
                    spec?.translationDiff || [
                      {
                        aspect: "Mesin Solver PDE",
                        v1: "Chang–Cooper 1D implisit, ∫P(x,T)dx = 1",
                        v2: "Identik 100% (memakai solver yang sama)",
                      },
                      {
                        aspect: "Prioritas Drift μ",
                        v1: "0.30·TFI + 0.15·OBI (tanpa filter iceberg)",
                        v2: "0.36·TFI_eff + 0.09·OBI_λ (≥80% taker) + Iceberg Guard",
                      },
                      {
                        aspect: "Harga Wajar",
                        v1: "Mid-price murni & VWAP-20 rata",
                        v2: "Stoikov Micro-Price terfilter cancel (bobot ke sisi tipis)",
                      },
                      {
                        aspect: "Jarak Order Book",
                        v1: "Bobot rata w = 1 (top-20)",
                        v2: "Exponential Distance Decay w(d) = e^(−λd), tembok >0,5%–1% ≈ 0",
                      },
                    ]
                  ).map((row) => (
                    <tr key={row.aspect}>
                      <td className="font-semibold text-ink">{row.aspect}</td>
                      <td className="text-muted">{row.v1}</td>
                      <td className="text-pine-800 font-medium">{row.v2}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
