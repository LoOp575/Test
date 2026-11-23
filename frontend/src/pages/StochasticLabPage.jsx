import React, { useMemo, useState } from "react";
import { Activity, BarChart3, BrainCircuit, Play, RefreshCw, Waves } from "lucide-react";

const CONFIG = {
  "random-walk": { title: "Random Walk Engine", subtitle: "Discrete Gaussian random-walk Monte Carlo", endpoint: "/api/stochastic/random-walk", icon: Activity },
  "brownian": { title: "Brownian Motion Engine", subtitle: "Continuous-time Brownian motion approximation", endpoint: "/api/stochastic/brownian", icon: Waves },
  "pattern-detector": { title: "Stochastic Pattern Detector", subtitle: "Statistical diagnostics on a supplied return series", endpoint: "/api/stochastic/pattern-detector", icon: BrainCircuit },
};

function Metric({ label, value }) {
  return <div className="rounded-xl border border-line bg-card p-3"><div className="text-[10px] uppercase tracking-wider text-muted">{label}</div><div className="mt-1 text-lg font-semibold font-mono text-ink break-all">{value ?? "—"}</div></div>;
}
function fmt(v) { return Number.isFinite(Number(v)) ? Number(v).toLocaleString("en-US", {maximumFractionDigits: 6}) : "—"; }

export default function StochasticLabPage({ mode = "random-walk" }) {
  const cfg = CONFIG[mode] || CONFIG["random-walk"];
  const Icon = cfg.icon;
  const [startPrice, setStartPrice] = useState("100");
  const [sigma, setSigma] = useState("0.02");
  const [periods, setPeriods] = useState("30");
  const [paths, setPaths] = useState("1000");
  const [horizon, setHorizon] = useState("1");
  const [steps, setSteps] = useState("100");
  const [returnsText, setReturnsText] = useState("0.012, -0.004, 0.008, -0.006, 0.003, 0.009, -0.011, 0.004, 0.006, -0.002, 0.007, -0.005, 0.002, 0.010, -0.008, 0.003, 0.005, -0.004, 0.006, -0.001, 0.009, -0.003, 0.004, -0.006, 0.008, 0.002, -0.005, 0.007, -0.004, 0.003");
  const [lag, setLag] = useState("4");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const payload = useMemo(() => mode === "random-walk"
    ? {startPrice:Number(startPrice), sigma:Number(sigma), periods:Number(periods), paths:Number(paths), seed:42}
    : mode === "brownian"
    ? {horizon:Number(horizon), sigma:Number(sigma), steps:Number(steps), paths:Number(paths), seed:42}
    : {returns:returnsText.split(/[\s,;]+/).filter(Boolean).map(Number), lag:Number(lag)}, 
    [mode,startPrice,sigma,periods,paths,horizon,steps,returnsText,lag]);
  async function run() {
    setBusy(true); setError(""); setResult(null);
    try {
      const res = await fetch(cfg.endpoint, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
      const body = await res.json();
      if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Permintaan gagal diproses.");
      setResult(body);
    } catch (e) { setError(e.message || "Tidak dapat menghubungi API."); }
    finally { setBusy(false); }
  }
  const s = result?.summary;
  return <section className="mx-auto max-w-7xl px-4 sm:px-6 py-8 space-y-6">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div><div className="flex items-center gap-2 text-pine-700 text-xs font-semibold uppercase tracking-wider"><Icon size={15}/> Standalone stochastic lab</div>
        <h1 className="mt-2 font-display text-3xl font-semibold text-ink">{cfg.title}</h1>
        <p className="mt-1 text-sm text-muted">{cfg.subtitle}. Modul ini berjalan mandiri dan tidak memanggil menu analisis lain.</p></div>
      <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-800">Independent engine</span>
    </div>
    <div className="grid gap-5 lg:grid-cols-[360px_1fr]">
      <div className="rounded-2xl border border-line bg-card p-5 space-y-4">
        <h2 className="font-semibold text-ink">Parameter simulasi</h2>
        {mode === "random-walk" && <>
          <label className="block text-xs text-muted">Harga awal<input className="mt-1 w-full rounded-lg border border-line bg-paper p-2.5 text-sm text-ink" type="number" min="0.000001" value={startPrice} onChange={e=>setStartPrice(e.target.value)}/></label>
          <label className="block text-xs text-muted">Volatilitas per langkah (σ relatif)<input className="mt-1 w-full rounded-lg border border-line bg-paper p-2.5 text-sm text-ink" type="number" step="0.001" min="0.000001" max="2" value={sigma} onChange={e=>setSigma(e.target.value)}/></label>
          <label className="block text-xs text-muted">Jumlah langkah<input className="mt-1 w-full rounded-lg border border-line bg-paper p-2.5 text-sm text-ink" type="number" min="1" max="500" value={periods} onChange={e=>setPeriods(e.target.value)}/></label>
        </>}
        {mode === "brownian" && <>
          <label className="block text-xs text-muted">Horizon waktu<input className="mt-1 w-full rounded-lg border border-line bg-paper p-2.5 text-sm text-ink" type="number" min="0.000001" value={horizon} onChange={e=>setHorizon(e.target.value)}/></label>
          <label className="block text-xs text-muted">Koefisien σ<input className="mt-1 w-full rounded-lg border border-line bg-paper p-2.5 text-sm text-ink" type="number" min="0.000001" value={sigma} onChange={e=>setSigma(e.target.value)}/></label>
          <label className="block text-xs text-muted">Diskretisasi langkah<input className="mt-1 w-full rounded-lg border border-line bg-paper p-2.5 text-sm text-ink" type="number" min="2" max="2000" value={steps} onChange={e=>setSteps(e.target.value)}/></label>
        </>}
        {mode === "pattern-detector" && <>
          <label className="block text-xs text-muted">Return series (pisahkan dengan koma, spasi, atau baris baru)<textarea className="mt-1 min-h-44 w-full rounded-lg border border-line bg-paper p-2.5 text-xs font-mono text-ink" value={returnsText} onChange={e=>setReturnsText(e.target.value)}/></label>
          <label className="block text-xs text-muted">Lag variance ratio<input className="mt-1 w-full rounded-lg border border-line bg-paper p-2.5 text-sm text-ink" type="number" min="2" max="30" value={lag} onChange={e=>setLag(e.target.value)}/></label>
        </>}
        {mode !== "pattern-detector" && <label className="block text-xs text-muted">Jumlah simulasi Monte Carlo<input className="mt-1 w-full rounded-lg border border-line bg-paper p-2.5 text-sm text-ink" type="number" min="100" max={mode==="brownian"?"5000":"10000"} step="100" value={paths} onChange={e=>setPaths(e.target.value)}/></label>}
        <button onClick={run} disabled={busy} className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-pine-800 px-4 py-3 text-sm font-semibold text-white hover:bg-pine-700 disabled:opacity-60">{busy?<RefreshCw size={16} className="animate-spin"/>:<Play size={16}/>} {busy?"Menghitung...":"Jalankan engine"}</button>
        <p className="text-[11px] leading-5 text-muted">Simulasi memakai seed tetap (42) agar hasil dapat direproduksi. Ubah seed melalui API bila diperlukan.</p>
      </div>
      <div className="space-y-4">
        {!result && !error && <div className="h-full min-h-64 rounded-2xl border border-dashed border-line bg-card/50 grid place-items-center p-8 text-center"><div><BarChart3 className="mx-auto text-muted" size={28}/><p className="mt-3 text-sm font-medium text-ink">Hasil belum tersedia</p><p className="mt-1 text-xs text-muted">Atur parameter lalu jalankan engine mandiri ini.</p></div></div>}
        {error && <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>}
        {s && <div className="rounded-2xl border border-line bg-card p-5 space-y-4">
          <div><div className="text-xs uppercase tracking-wider text-muted">Output · {result.engine}</div><h2 className="mt-1 text-xl font-semibold text-ink">{mode==="pattern-detector"?s.verdict:"Ringkasan simulasi"}</h2></div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {Object.entries(s).filter(([k,v])=>typeof v==="number").map(([k,v])=><Metric key={k} label={k.replace(/([A-Z])/g," $1")} value={fmt(v)}/>)}
          </div>
          {mode==="pattern-detector" && <p className="rounded-xl bg-paper p-3 text-sm text-muted">{s.interpretation}</p>}
          {result.samplePaths && <div><h3 className="text-sm font-semibold text-ink mb-2">12 lintasan sampel</h3><div className="max-h-52 overflow-auto rounded-lg bg-paper p-3 text-[10px] font-mono text-muted space-y-1">{result.samplePaths.map((p,i)=><div key={i}>Path {i+1}: {p.slice(0,8).map(fmt).join(" · ")} ... {fmt(p[p.length-1])}</div>)}</div></div>}
          {result.warning && <p className="text-xs leading-5 text-amber-800 bg-amber-50 rounded-lg p-3">{result.warning}</p>}
          {result.limitations && <ul className="list-disc pl-5 text-xs text-muted space-y-1">{result.limitations.map(x=><li key={x}>{x}</li>)}</ul>}
        </div>}
      </div>
    </div>
  </section>;
}
