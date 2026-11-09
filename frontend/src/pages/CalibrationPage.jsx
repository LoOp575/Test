import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Copy,
  Download,
  FlaskConical,
  History,
  Info,
  ListFilter,
  Play,
  ShieldCheck,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";
import {
  fetchCalibrationFormulas,
  fetchCalibrationHistory,
  runCalibration,
  runCalibrationSweep,
} from "../lib/api";

const LS_KEY = "meridian.calibration.runs";

const LOOKBACKS = [
  { value: 365, label: "1 tahun (365 candle)" },
  { value: 730, label: "2 tahun (730 candle)" },
  { value: 1000, label: "3 tahun (1000 candle, maks)" },
];
const STEPS = [
  { value: 3, label: "Setiap 3 hari (sampel padat)" },
  { value: 7, label: "Setiap 7 hari (mingguan)" },
  { value: 14, label: "Setiap 14 hari (dua mingguan)" },
];
const LABELS = [
  { value: "pos", label: "y = 1 bila organik 30d ke depan > 0" },
  { value: "strong", label: "y = 1 bila organik 30d ke depan ≥ +15%" },
];

function fmt(v, digits = 3) {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(digits);
}
function pct(v, digits = 1) {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : `${(Number(v) * 100).toFixed(digits)}%`;
}
function tsLocal(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
}

function loadLocalRuns() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function toHistoryEntry(rep, source) {
  const v = rep?.validation || {};
  const h = rep?.currentHeuristic || {};
  const p = rep?.params || {};
  return {
    id: `${source}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    ts: rep?.generatedAt || new Date().toISOString(),
    source,
    label: p.label ?? rep?.label ?? "—",
    lookbackDays: p.lookbackDays ?? null,
    stepDays: p.stepDays ?? null,
    samples: rep?.samples ?? null,
    l2: rep?.l2 ?? null,
    baseRateValidation: rep?.baseRateValidation ?? null,
    fitted: { brier: v.brier ?? null, auc: v.auc ?? null, logloss: v.logloss ?? null },
    heuristic: { brier: h.validationBrier ?? null, auc: h.validationAuc ?? null },
    verdict: rep?.verdict?.status ?? "—",
  };
}

function elapsedLabel(seconds) {
  if (!seconds && seconds !== 0) return "";
  return `${seconds.toFixed(0)} dtk`;
}

export default function CalibrationPage() {
  const [formulas, setFormulas] = useState(null);
  const [formulaError, setFormulaError] = useState(null);
  const [lookback, setLookback] = useState(730);
  const [stepDays, setStepDays] = useState(7);
  const [label, setLabel] = useState("pos");
  const [busy, setBusy] = useState(null); // "run" | "sweep" | null
  const [elapsed, setElapsed] = useState(0);
  const [runError, setRunError] = useState(null);
  const [run, setRun] = useState(null);
  const [sweep, setSweep] = useState(null);
  const [sweepError, setSweepError] = useState(null);
  const [localRuns, setLocalRuns] = useState(() => loadLocalRuns());
  const [serverRuns, setServerRuns] = useState([]);
  const [expanded, setExpanded] = useState({});
  const [copied, setCopied] = useState(null); // "block" | "history" | null
  const timerRef = useRef(null);

  // Persist local history
  useEffect(() => {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(localRuns.slice(0, 25)));
    } catch {
      /* storage penuh — riwayat lokal tidak tersimpan */
    }
  }, [localRuns]);

  // Elapsed timer while busy
  useEffect(() => {
    if (!busy) {
      setElapsed(0);
      return undefined;
    }
    const started = Date.now();
    timerRef.current = setInterval(() => setElapsed((Date.now() - started) / 1000), 250);
    return () => clearInterval(timerRef.current);
  }, [busy]);

  // Load formula metadata + server-side history on mount
  useEffect(() => {
    let alive = true;
    fetchCalibrationFormulas()
      .then((d) => alive && setFormulas(d))
      .catch((e) => alive && setFormulaError(e.message || "Gagal memuat formula"));
    fetchCalibrationHistory(25)
      .then((d) => alive && setServerRuns(Array.isArray(d?.runs) ? d.runs.map((r) => ({ ...r, source: "server" })) : []))
      .catch(() => alive && setServerRuns([]));
    return () => {
      alive = false;
    };
  }, []);

  const recordRun = useCallback((rep, sourceTag = "browser") => {
    setLocalRuns((prev) => [toHistoryEntry(rep, sourceTag), ...prev].slice(0, 25));
  }, []);

  const handleRun = useCallback(async () => {
    if (busy) return;
    setBusy("run");
    setRunError(null);
    try {
      const rep = await runCalibration({ lookbackDays: lookback, stepDays, label });
      setRun(rep);
      if (rep?.ok) recordRun(rep);
      else setRunError(rep?.reason || "Kalibrasi gagal (lihat detail).");
    } catch (e) {
      setRunError(e.message || "Kalibrasi gagal.");
    } finally {
      setBusy(null);
    }
  }, [busy, lookback, stepDays, label, recordRun]);

  const handleSweep = useCallback(async () => {
    if (busy) return;
    setBusy("sweep");
    setSweepError(null);
    try {
      const rep = await runCalibrationSweep({ lookbackDays: lookback, stepDays });
      setSweep(rep);
      if (rep?.ok) {
        const best = (rep.candidates || []).find((c) => c.label === rep.bestLabel);
        if (best) {
          recordRun(
            { ...best, generatedAt: rep.generatedAt, params: { ...rep.params, label: best.label }, baseRateValidation: null },
            "browser"
          );
        }
      } else {
        setSweepError(rep?.candidates?.map((c) => `${c.label}: ${c.reason || "gagal"}`).join(" · ") || "Sweep gagal.");
      }
    } catch (e) {
      setSweepError(e.message || "Sweep gagal.");
    } finally {
      setBusy(null);
    }
  }, [busy, lookback, stepDays, recordRun]);

  const copyText = useCallback(async (text, tag) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(tag);
      window.setTimeout(() => setCopied(null), 1800);
    } catch {
      /* clipboard tidak tersedia */
    }
  }, []);

  const allRuns = useMemo(() => {
    const merged = [...localRuns.map((r) => ({ ...r, source: "browser" })), ...serverRuns];
    merged.sort((a, b) => String(b.ts).localeCompare(String(a.ts)));
    return merged.slice(0, 30);
  }, [localRuns, serverRuns]);

  const exportHistory = useCallback(() => {
    const blob = new Blob([JSON.stringify(allRuns, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "meridian-calibration-runs.json";
    a.click();
    URL.revokeObjectURL(url);
  }, [allRuns]);

  const reliabilityBins = useMemo(
    () => (run?.validation?.reliability || []).filter((b) => b.count > 0),
    [run]
  );

  const passFail = (ok) =>
    ok ? (
      <CheckCircle2 size={15} strokeWidth={2} className="text-up shrink-0 mt-0.5" />
    ) : (
      <XCircle size={15} strokeWidth={2} className="text-down shrink-0 mt-0.5" />
    );

  const metricRow = (name, fittedV, heurV, better /* "lower" | "higher" */) => {
    const fv = Number(fittedV);
    const hv = Number(heurV);
    const valid = Number.isFinite(fv) && Number.isFinite(hv);
    const fittedWins = valid && (better === "lower" ? fv < hv : fv > hv);
    return (
      <tr key={name}>
        <td className="font-medium text-ink">{name}</td>
        <td className="font-mono tabular-nums text-muted">{fmt(heurV, 4)}</td>
        <td className={`font-mono tabular-nums font-semibold ${valid ? (fittedWins ? "text-up" : "text-down") : "text-muted"}`}>
          {fmt(fittedV, 4)}
        </td>
        <td className="text-right">
          {valid ? (fittedWins ? <CheckCircle2 size={14} className="inline text-up" /> : <XCircle size={14} className="inline text-down" />) : "—"}
        </td>
      </tr>
    );
  };

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-8 md:py-10">
      {/* ---------- header ---------- */}
      <div className="border-b border-line pb-6 max-w-3xl">
        <p className="micro-label text-gold-600 flex items-center gap-2">
          <FlaskConical size={14} strokeWidth={2} /> Lab Kalibrasi p^acc
        </p>
        <h1 className="font-display text-4xl font-semibold tracking-tight mt-2">Kalibrasi rumus, transparan</h1>
        <p className="mt-3 text-[15px] leading-6 text-muted">
          Jalankan kalibrasi walk-forward langsung dari sini, catat hasilnya, bandingkan dengan{" "}
          <strong className="text-ink">pendekatan rumus yang sekarang dipakai</strong>, dan kalau hasilnya{" "}
          <strong className="text-ink">kurang</strong>, pencet tombol sweep — rumus alternatif dicari otomatis dari
          data yang sama. Tidak ada satu pun konstanta production yang berubah dari halaman ini.
        </p>
      </div>

      {/* ---------- aturan main ---------- */}
      <div className="mt-5 card p-4 flex flex-wrap items-start gap-3 border-pine-200">
        <ShieldCheck size={18} strokeWidth={1.75} className="text-pine-700 mt-0.5 shrink-0" />
        <div className="text-[12px] leading-5 text-ink/90 min-w-0">
          <p className="font-semibold text-pine-800">Aturan main (tidak bisa dinego)</p>
          <ul className="mt-1 list-disc list-inside space-y-0.5 text-muted">
            <li>Data hanya dari Binance live (<code className="font-mono text-[11px]">data-api.binance.vision</code> → fallback <code className="font-mono text-[11px]">api.binance.us</code>). Bukan data seed sintetis, bukan candle karangan.</li>
            <li>Walk-forward: train 75% awal (time-ordered), validasi 25% akhir. Tidak ada look-ahead.</li>
            <li>Fitted ≠ produksi. Penerapan rumus = perubahan kode + review manusia.</li>
            <li>Serverless punya ~60 detik; fetch dibatasi 1000 candle/simbol, 10 dtk timeout per fetch.</li>
          </ul>
        </div>
      </div>

      <div className="mt-6 grid lg:grid-cols-3 gap-6">
        {/* ---------- kolom kiri: formula sekarang + kontrol ---------- */}
        <div className="space-y-6">
          {/* Pendekatan rumus saat ini */}
          <section className="card p-5">
            <p className="micro-label flex items-center gap-2"><ListFilter size={13} strokeWidth={2} /> Pendekatan rumus saat ini</p>
            {formulaError && <p className="mt-3 text-[12px] text-down">Gagal memuat formula: {formulaError}</p>}
            {!formulas && !formulaError && <p className="mt-3 text-[12px] text-muted">Memuat…</p>}
            {formulas && (
              <>
                <p className="mt-3 text-[13px] font-semibold text-ink">{formulas.name}</p>
                <pre className="mt-2 formula-block !whitespace-pre-wrap !break-words text-[11px] leading-5">{formulas.form}</pre>
                <div className="mt-3 overflow-x-auto">
                  <table className="data-table w-full text-[12px]">
                    <thead>
                      <tr><th>Bobot</th><th className="text-right">Nilai</th></tr>
                    </thead>
                    <tbody>
                      {Object.entries(formulas.weights || {}).map(([k, v]) => (
                        <tr key={k}>
                          <td className="font-mono text-[11px]">{k}</td>
                          <td className="text-right font-mono tabular-nums">{Number(v).toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-3 text-[11px] leading-5 text-gold-700 flex items-start gap-1.5">
                  <Info size={12} className="mt-0.5 shrink-0" /> {formulas.status}
                </p>
              </>
            )}
          </section>

          {/* Kontrol */}
          <section className="card p-5">
            <p className="micro-label flex items-center gap-2"><Play size={13} strokeWidth={2} /> Jalankan kalibrasi</p>
            <div className="mt-3 space-y-3">
              <label className="block">
                <span className="micro-label !text-[9px]">Lookback</span>
                <select value={lookback} onChange={(e) => setLookback(Number(e.target.value))} className="mt-1 w-full rounded-lg border border-line bg-paper px-3 py-2 text-[13px] focus-ring" disabled={!!busy}>
                  {LOOKBACKS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="micro-label !text-[9px]">Stride anchor</span>
                <select value={stepDays} onChange={(e) => setStepDays(Number(e.target.value))} className="mt-1 w-full rounded-lg border border-line bg-paper px-3 py-2 text-[13px] focus-ring" disabled={!!busy}>
                  {STEPS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="micro-label !text-[9px]">Label target (y)</span>
                <select value={label} onChange={(e) => setLabel(e.target.value)} className="mt-1 w-full rounded-lg border border-line bg-paper px-3 py-2 text-[13px] focus-ring" disabled={!!busy}>
                  {LABELS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
            </div>
            <div className="mt-4 grid sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2 gap-2">
              <button className="btn-primary justify-center" onClick={handleRun} disabled={!!busy}>
                {busy === "run" ? `Kalibrasi… ${elapsedLabel(elapsed)}` : "Jalankan kalibrasi"}
              </button>
              <button className="btn-ghost justify-center" onClick={handleSweep} disabled={!!busy} title="Fit kedua label dalam satu fetch, ranking by validation log-loss">
                {busy === "sweep" ? `Sweep… ${elapsedLabel(elapsed)}` : "Cari rumus terbaik"}
              </button>
            </div>
            <p className="mt-2 text-[11px] leading-5 text-muted">
              <Sparkles size={11} className="inline -mt-0.5" /> "Cari rumus terbaik" = auto-sweep kedua varian label,
              diurutkan by validation log-loss. Jujur dikatakan kalau tidak ada yang lolos.
            </p>
            {(runError || sweepError) && (
              <p className="mt-3 text-[12px] leading-5 text-down flex items-start gap-1.5">
                <CircleAlert size={13} className="mt-0.5 shrink-0" /> {runError || sweepError}
              </p>
            )}
          </section>

          {/* Riwayat */}
          <section className="card p-5">
            <div className="flex items-center justify-between gap-2">
              <p className="micro-label flex items-center gap-2"><History size={13} strokeWidth={2} /> Riwayat kalibrasi</p>
              <div className="flex items-center gap-1">
                <button className="p-1.5 rounded-lg text-muted hover:text-ink hover:bg-pine-50 transition-colors focus-ring" title="Salin semua JSON" onClick={() => copyText(JSON.stringify(allRuns, null, 2), "history")}>
                  {copied === "history" ? <CheckCircle2 size={15} className="text-up" /> : <Copy size={15} />}
                </button>
                <button className="p-1.5 rounded-lg text-muted hover:text-ink hover:bg-pine-50 transition-colors focus-ring" title="Unduh JSON" onClick={exportHistory}>
                  <Download size={15} />
                </button>
                <button className="p-1.5 rounded-lg text-muted hover:text-down hover:bg-pine-50 transition-colors focus-ring" title="Hapus riwayat browser" onClick={() => setLocalRuns([])}>
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
            <p className="mt-1.5 text-[10.5px] leading-4 text-faint">
              Riwayat browser tersimpan di perangkat ini. Riwayat "server" hanya di instance yang sedang jalan dan hilang saat redeploy.
            </p>
            {allRuns.length === 0 && <p className="mt-3 text-[12px] text-muted">Belum ada run tercatat.</p>}
            <ul className="mt-2 divide-y divide-line max-h-72 overflow-y-auto">
              {allRuns.map((r) => {
                const open = !!expanded[r.id];
                return (
                  <li key={r.id} className="py-2">
                    <button className="w-full flex items-center gap-2 text-left focus-ring rounded" onClick={() => setExpanded((s) => ({ ...s, [r.id]: !open }))}>
                      {open ? <ChevronDown size={14} className="shrink-0 text-muted" /> : <ChevronRight size={14} className="shrink-0 text-muted" />}
                      <span className={`chip !text-[9px] ${r.verdict === "lulus" ? "!border-up !text-up" : r.verdict === "kurang" ? "!border-down !text-down" : ""}`}>{r.verdict}</span>
                      <span className="font-mono text-[10.5px] text-muted">{r.label} · LB{r.lookbackDays ?? "?"} · s{r.stepDays ?? "?"}</span>
                      <span className="ml-auto font-mono text-[10.5px] text-faint">{tsLocal(r.ts)}</span>
                    </button>
                    {open && (
                      <div className="mt-2 pl-6 text-[11px] leading-5 text-muted grid grid-cols-2 gap-x-4 gap-y-1">
                        <span>Brier: <b className="text-ink">{fmt(r.fitted?.brier, 4)}</b> (heur {fmt(r.heuristic?.brier, 4)})</span>
                        <span>AUC: <b className="text-ink">{fmt(r.fitted?.auc, 3)}</b> (heur {fmt(r.heuristic?.auc, 3)})</span>
                        <span>Sampel: <b className="text-ink">{r.samples ?? "—"}</b></span>
                        <span>L2: <b className="text-ink">{r.l2 ?? "—"}</b></span>
                        <span>Base rate: <b className="text-ink">{pct(r.baseRateValidation, 1)}</b></span>
                        <span>Sumber: <b className="text-ink">{r.source === "server" ? "server (volatile)" : "browser"}</b></span>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        </div>

        {/* ---------- kolom kanan: hasil ---------- */}
        <div className="lg:col-span-2 space-y-6">
          {/* Hasil run terakhir */}
          <section className="card p-5">
            <p className="micro-label">Cek: rumus vs hasil pengukuran</p>

            {!run && !busy && (
              <div className="mt-4 rounded-xl border border-dashed border-lineStrong px-5 py-8 text-center">
                <FlaskConical size={26} strokeWidth={1.5} className="mx-auto text-faint" />
                <p className="mt-3 text-[13px] text-muted">Belum ada hasil. Pilih parameter lalu tekan <b className="text-ink">Jalankan kalibrasi</b>.</p>
                <p className="mt-1 text-[11px] text-faint">Fetch klines live ~15–45 dtk untuk 40 simbol; hasilnya langsung dicatat di riwayat.</p>
              </div>
            )}
            {busy === "run" && (
              <div className="mt-4 rounded-xl border border-line px-5 py-8 text-center">
                <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-line border-t-pine-700" />
                <p className="mt-3 text-[13px] text-muted">Mengambil klines live & fit walk-forward… {elapsedLabel(elapsed)}</p>
              </div>
            )}

            {run && run.ok && (
              <>
                {/* verdict banner */}
                <div className={`mt-4 rounded-xl border px-4 py-3 flex items-start gap-3 ${run.verdict?.status === "lulus" ? "border-up bg-up/5" : "border-down bg-down/5"}`}>
                  {run.verdict?.status === "lulus"
                    ? <ShieldCheck size={20} className="text-up shrink-0 mt-0.5" />
                    : <CircleAlert size={20} className="text-down shrink-0 mt-0.5" />}
                  <div className="min-w-0">
                    <p className={`font-display text-lg font-semibold tracking-tight ${run.verdict?.status === "lulus" ? "text-up" : "text-down"}`}>
                      {run.verdict?.status === "lulus" ? "LULUS — formula baru mengalahkan heuristic" : "KURANG — heuristic belum terkalahkan"}
                    </p>
                    <p className="text-[11px] text-muted mt-0.5">
                      {run.samples} anchor · validasi {run.validationSamples} sampel · base rate validasi {pct(run.baseRateValidation, 1)} · L2={run.l2} · anchor {run.anchorFirst} → {run.anchorLast}
                    </p>
                  </div>
                </div>

                {/* kriteria */}
                <ul className="mt-4 grid sm:grid-cols-3 gap-2">
                  {Object.entries(run.verdict?.criteria || {}).map(([key, c]) => (
                    <li key={key} className="rounded-xl border border-line bg-paper px-3 py-2 flex items-start gap-2">
                      {passFail(c.ok)}
                      <span className="text-[10.5px] leading-4 text-muted">{c.detail}</span>
                    </li>
                  ))}
                </ul>
                {(run.verdict?.notes || []).map((n, i) => (
                  <p key={i} className="mt-2 text-[11px] leading-5 text-gold-700 flex items-start gap-1.5"><Info size={12} className="mt-0.5 shrink-0" />{n}</p>
                ))}

                {/* tabel metrik */}
                <div className="mt-4 overflow-x-auto">
                  <table className="data-table w-full text-[12px]">
                    <thead>
                      <tr><th>Metrik</th><th>Heuristic sekarang</th><th>Terkalibrasi (L2)</th><th className="text-right">Unggul</th></tr>
                    </thead>
                    <tbody>
                      {metricRow("Brier score ↓", run.validation?.brier, run.currentHeuristic?.validationBrier, "lower")}
                      {metricRow("AUC ↑", run.validation?.auc, run.currentHeuristic?.validationAuc, "higher")}
                      <tr>
                        <td className="font-medium text-ink">Log-loss ↓</td>
                        <td className="font-mono tabular-nums text-muted">—</td>
                        <td className="font-mono tabular-nums">{fmt(run.validation?.logloss, 4)}</td>
                        <td className="text-right text-faint">ref</td>
                      </tr>
                      <tr>
                        <td className="font-medium text-ink">Precision @ p≥0.6</td>
                        <td className="font-mono tabular-nums text-muted">
                          {run.currentHeuristic?.["predictions_ge_0.6"] ? `${pct(run.currentHeuristic?.["predictions_ge_0.6"]?.precision, 1)} (n=${run.currentHeuristic?.["predictions_ge_0.6"]?.count})` : "—"}
                        </td>
                        <td className="font-mono tabular-nums">
                          {run.validation?.["p_ge_0.6"] ? `${pct(run.validation?.["p_ge_0.6"]?.precision, 1)} (n=${run.validation?.["p_ge_0.6"]?.count})` : "—"}
                        </td>
                        <td className="text-right text-faint">vs base {pct(run.baseRateValidation, 1)}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                {/* reliability chart */}
                {reliabilityBins.length > 0 && (
                  <div className="mt-5">
                    <p className="micro-label !text-[9px]">Reliability (prediksi vs realisasi, validasi)</p>
                    <div className="mt-2 h-52">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={reliabilityBins.map((b) => ({ name: `${Math.round(b.bin * 10)}`, predMean: b.predMean, obsFreq: b.obsFreq, count: b.count }))} margin={{ top: 6, right: 4, left: -14, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e5e0d5)" vertical={false} />
                          <XAxis dataKey="name" tick={{ fontSize: 10 }} label={{ value: "desil probabilitas", position: "insideBottom", offset: -2, fontSize: 9 }} />
                          <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} />
                          <Tooltip cursor={{ fill: "rgba(20,60,50,0.06)" }} contentStyle={{ fontSize: 11, borderRadius: 10 }} />
                          <Bar dataKey="predMean" name="Prediksi mean" fill="#1f4d3f" radius={[3, 3, 0, 0]} />
                          <Bar dataKey="obsFreq" name="Frekuensi aktual" fill="#c9a227" radius={[3, 3, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                    <p className="mt-1 text-[10px] text-faint">Bar emas menempel bar hijau = terkalibrasi baik. Desil 0–9 = rentang p 0.0–0.1 s.d. 0.9–1.0.</p>
                  </div>
                )}

                {/* saran formula */}
                {run.suggestedBlock && (
                  <div className="mt-5">
                    <div className="flex items-center justify-between gap-2">
                      <p className="micro-label !text-[9px]">Saran formula (belum diterapkan)</p>
                      <button className="btn-ghost !px-2.5 !py-1 !text-[10.5px]" onClick={() => copyText(run.suggestedBlock, "block")}>
                        {copied === "block" ? <CheckCircle2 size={12} /> : <Copy size={12} />} {copied === "block" ? "Tersalin" : "Salin saran"}
                      </button>
                    </div>
                    <pre className="mt-2 formula-block !whitespace-pre-wrap !break-words text-[10.5px] leading-5 max-h-64 overflow-auto">{run.suggestedBlock}</pre>
                    <p className="mt-1.5 text-[10.5px] leading-4 text-muted">{run.note}</p>
                  </div>
                )}

                {/* skip + per-symbol */}
                {(run.skipped?.length > 0 || run.universe?.length > 0) && (
                  <p className="mt-4 text-[10.5px] leading-4 text-faint">
                    Universe live: {run.universeSize ?? run.universe?.length ?? 0} simbol{run.universe ? ` (${run.universe.join(", ")})` : ""}.{" "}
                    {run.skipped?.length > 0 && `Dilewati: ${run.skipped.map((s) => s.symbol).join(", ")} — ${run.skipped[0]?.reason ?? "fetch gagal"}.`}
                  </p>
                )}
              </>
            )}
            {run && !run.ok && (
              <p className="mt-4 text-[12px] leading-5 text-down flex items-start gap-1.5">
                <CircleAlert size={14} className="mt-0.5 shrink-0" /> {run.reason}
              </p>
            )}
          </section>

          {/* Hasil sweep */}
          <section className="card p-5">
            <p className="micro-label flex items-center gap-2"><Sparkles size={13} strokeWidth={2} /> Hasil "cari rumus terbaik" (sweep)</p>
            {!sweep && busy !== "sweep" && (
              <p className="mt-3 text-[12px] text-muted">Belum ada sweep. Tombol ini fetch sekali, fit kedua varian label, lalu ranking by validation log-loss. Kalau dua-duanya kurang, itu jawabannya — dan tetap dicatat.</p>
            )}
            {busy === "sweep" && (
              <div className="mt-4 flex items-center gap-3 text-[12px] text-muted">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-line border-t-pine-700" /> Menyapu label pos &amp; strong… {elapsedLabel(elapsed)}
              </div>
            )}
            {sweep && (
              <>
                {sweep.ok ? (
                  <>
                    <p className="mt-3 text-[12px] leading-5 text-muted">
                      Pemenang (by validation log-loss):{" "}
                      <strong className="text-ink">label = {sweep.bestLabel}</strong>
                      {" "}(log-loss {fmt(sweep.bestValidationLogloss, 4)}). Verdict pemenang:{" "}
                      <strong className={sweep.verdict?.status === "lulus" ? "text-up" : "text-down"}>{sweep.verdict?.status ?? "—"}</strong>.
                    </p>
                    <div className="mt-3 overflow-x-auto">
                      <table className="data-table w-full text-[12px]">
                        <thead>
                          <tr><th>Label</th><th>Sampel</th><th>Brier</th><th>AUC</th><th>Log-loss</th><th>L2</th><th>Verdict</th></tr>
                        </thead>
                        <tbody>
                          {(sweep.candidates || []).map((c) =>
                            c.ok === false ? (
                              <tr key={c.label}><td className="font-mono">{c.label}</td><td colSpan={6} className="text-down">gagal: {c.reason}</td></tr>
                            ) : (
                              <tr key={c.label} className={c.label === sweep.bestLabel ? "bg-pine-50/60" : ""}>
                                <td className="font-mono font-semibold">{c.label}{c.label === sweep.bestLabel ? " ★" : ""}</td>
                                <td className="font-mono tabular-nums">{c.samples}</td>
                                <td className="font-mono tabular-nums">{fmt(c.validation?.brier, 4)}</td>
                                <td className="font-mono tabular-nums">{fmt(c.validation?.auc, 3)}</td>
                                <td className="font-mono tabular-nums">{fmt(c.validation?.logloss, 4)}</td>
                                <td className="font-mono tabular-nums">{c.l2}</td>
                                <td className={c.verdict?.status === "lulus" ? "text-up font-semibold" : "text-down font-semibold"}>{c.verdict?.status ?? "—"}</td>
                              </tr>
                            )
                          )}
                        </tbody>
                      </table>
                    </div>
                    {(sweep.candidates || []).every((c) => c.verdict?.status !== "lulus") && (
                      <p className="mt-3 text-[11.5px] leading-5 text-gold-700 flex items-start gap-1.5">
                        <CircleAlert size={13} className="mt-0.5 shrink-0" /> Tidak ada kandidat yang lolos — itulah jawaban sweep ini.
                        Artinya pendekatan p^acc saat ini belum bisa digantikan oleh fit pada universe/lookback ini; jangan paksa terapkan.
                      </p>
                    )}
                    <p className="mt-2 text-[10.5px] leading-4 text-faint">{sweep.warning}</p>
                  </>
                ) : (
                  <p className="mt-3 text-[12px] text-down">Sweep gagal total: {(sweep.candidates || []).map((c) => `${c.label}: ${c.reason}`).join(" · ")}</p>
                )}
              </>
            )}
          </section>

          {/* footer note */}
          <p className="text-[11px] leading-5 text-faint px-1">
            Penerapan hasil kalibrasi ke kode produksi = PR terpisah yang direview manusia (formula freeze).
            Halaman ini sengaja tidak punya tombol "terapkan". Lihat <a className="underline hover:text-ink" href="/methodology#limits">Methodology → Limitations</a>.
          </p>
        </div>
      </div>
    </div>
  );
}
