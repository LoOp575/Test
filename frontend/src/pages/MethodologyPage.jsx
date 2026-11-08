import React from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, BookOpen, Database, FlaskConical, ShieldAlert } from "lucide-react";

const SECTIONS = [
  { id: "overview", label: "01 · What this is" },
  { id: "data", label: "02 · Data inputs" },
  { id: "model1", label: "03 · Model 1 — demand quality" },
  { id: "model2", label: "04 · Model 2 — instability flags" },
  { id: "verdicts", label: "05 · Verdicts & filters" },
  { id: "mapping", label: "06 · Slide → crypto mapping" },
  { id: "limits", label: "07 · Limitations" },
];

function H2({ id, children }) {
  return (
    <h2 id={id} className="font-display text-2xl font-semibold tracking-tight text-ink mt-2 scroll-mt-24">
      {children}
    </h2>
  );
}

function Formula({ children }) {
  return <div className="formula-block mt-3">{children}</div>;
}

export default function MethodologyPage() {
  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-8 md:py-10">
      <Link to="/" className="inline-flex items-center gap-2 text-[12px] font-medium text-muted hover:text-pine-700 transition-colors focus-ring rounded">
        <ArrowLeft size={14} strokeWidth={2} /> Back to screener
      </Link>

      <div className="mt-5 border-b border-line pb-6 max-w-3xl">
        <p className="micro-label text-gold-600">Methodology</p>
        <h1 className="font-display text-4xl font-semibold tracking-tight mt-2">How Meridian scores an asset</h1>
        <p className="mt-3 text-[15px] leading-6 text-muted">
          Meridian is a <strong className="text-ink">documented adaptation</strong> of two quantitative models —
          a deferred-revenue quality model (for long candidates) and a filing-instability model (for avoiding
          red flags). The originals were built for US equities. Crypto has no 10-Qs, no deferred revenue and no
          GAAP, so each input was re-mapped to the closest observable market analogue. Nothing here is hidden:
          every constant is visible in <code className="font-mono text-[12px] bg-pine-50 px-1.5 py-0.5 rounded">backend/screening.py</code>.
        </p>
      </div>

      <div className="mt-8 grid lg:grid-cols-4 gap-8">
        {/* ------------ TOC ------------ */}
        <aside className="lg:col-span-1">
          <div className="card p-5 lg:sticky lg:top-24">
            <p className="micro-label !text-[9px] flex items-center gap-2"><BookOpen size={13} strokeWidth={2} /> Contents</p>
            <nav className="mt-3 flex flex-col">
              {SECTIONS.map((s) => (
                <a key={s.id} href={`#${s.id}`} className="text-[12.5px] text-muted hover:text-pine-700 py-1.5 border-b border-line last:border-0 transition-colors">
                  {s.label}
                </a>
              ))}
            </nav>
          </div>
        </aside>

        {/* ------------ body ------------ */}
        <article className="lg:col-span-3 space-y-10 max-w-3xl">
          <section>
            <H2 id="overview">01 · What this is — and what it is not</H2>
            <p className="mt-3 text-[14px] leading-6 text-ink/90">
              Meridian ranks liquid USDT pairs by <em>demand quality</em>: is genuine, unit-level demand for the asset
              growing faster than its price — and is that growth healthy enough to hold? It is a screen, not a trading
              bot. It does not predict price. It surfaces names where accumulation evidence is strongest, and flags names
              whose price action looks like hype or distress rather than adoption.
            </p>
          </section>

          <section>
            <H2 id="data">02 · Data inputs</H2>
            <p className="mt-3 text-[14px] leading-6 text-ink/90 flex items-start gap-2">
              <Database size={16} strokeWidth={1.75} className="mt-1 shrink-0 text-pine-600" />
              <span>
                Daily candles (120 bars): open, high, low, close, base volume, quote volume, trade count and
                taker-buy volume — from Binance spot, falling back to MEXC spot, Binance futures, then CoinGecko.
                If every live source is unreachable the app serves a clearly-labelled, deterministic demo dataset
                (you will see a “Demo data” banner). Index <code className="font-mono text-[12px]">i</code> = asset, <code className="font-mono text-[12px]">t</code> = day.
              </span>
            </p>
          </section>

          <section>
            <H2 id="model1">03 · Model 1 — Demand quality (deferred-revenue analog)</H2>
            <p className="mt-3 text-[14px] leading-6 text-ink/90">
              The slides measure deferred revenue (money collected but not yet recognised) as a leading indicator of
              future revenue. The crypto analogue is <strong>unit (base-asset) volume growth, cleaned of price and mix</strong> —
              real demand accumulating before price reflects it.
            </p>
            <Formula>G↑rev  = (P_t / P_t−30) − 1                              {/* revenue-growth analog */}</Formula>
            <Formula>G↑DRⁿ = (QV_t / QV_t−30) − 1                              {/* reported USD demand growth */}</Formula>
            <Formula>G↑org = (V_t / V_t−30) − 1                                {/* ORGANIC unit demand growth */}</Formula>
            <Formula>Distortion = G↑DRⁿ − G↑org                                {/* price & mix stripped out */}</Formula>
            <Formula>A↑DR  = G↑org,t − G↑org,t−30                              {/* acceleration vs. prior window */}</Formula>
            <Formula>D↑lead = G↑org − G↑rev                                     {/* demand lead over price — identical form to slides */}</Formula>
            <Formula>S↑flow = exp(−3.0 · max(0, G↑trades,t−30 − G↑trades,t))     {/* participation-stability (churn analog) */}</Formula>
            <Formula>Q↑ret = 0.5·(NRR* − 1) + 0.8·GRR* − 1.1·Churn*               {/* retention quality: NRR/GRR analogs */}</Formula>
            <Formula>C↑cash = Σ takerBuyBase(30d) / Σ volume(30d)                {/* cash-conversion analog */}</Formula>
            <Formula>Q↑DR = sat(G↑org)₊ · S↑flow · (1 + Q↑ret)₊ · (1 + C↑cash)₊ → 0-100</Formula>
            <Formula>GAP↑DR = D↑lead / (1 + G↑rev)                              {/* under-recognition gap vs price */}</Formula>
            <Formula>S↑DR = w-geom-mean(sat(G↑org), sat(A↑DR), sat(D↑lead), sat(GAP↑DR))   {/* 0.4/0.2/0.2/0.2 */}</Formula>
            <Formula>p↑acc = σ(−0.75 + 1.7·g + 1.3·a + 1.5·d + 1.2·gap + 1.2·(C↑cash−0.5) + 0.9·(S↑flow−0.7))</Formula>
            <p className="mt-3 text-[12px] leading-5 text-muted">
              sat(x) = x/(1+|x|) bounds every raw growth figure to (−1, 1). NRR* = clamp(1 + G↑org, 0, 2) — “net demand retention”;
              GRR* = share of days with taker-buy ratio ≥ 0.5 — “gross retention of buying pressure”;
              Churn* = max(0, −G↑trades) — participation exodus. Where the slides would multiply raw factors, Meridian uses
              a weighted geometric mean so one weak factor cannot be rescued by three strong ones, and scores stay comparable across assets.
            </p>
          </section>

          <section>
            <H2 id="model2">04 · Model 2 — Instability flags (amendment analog)</H2>
            <p className="mt-3 text-[14px] leading-6 text-ink/90">
              The slides count amended filings (10-Q/A, 10-K/A, 8-K/A) and weight their materiality. Crypto has no audited
              amendments, so the analogue is <strong>abnormal-move days detected on OLS-detrended log volume and 2.5σ price moves</strong>:
              what keeps a trainwreck in the window, not a one-off.
            </p>
            <Formula>Event(t) = |r_t| ≥ max(2.5σ_90d, 5.5%)  OR  |resid(log QV_t)| ≥ 2.5σ_resid   {/* OLS-detrended */}</Formula>
            <Formula>F↑amend = n_events / 30                                    {/* amendment frequency analog */}</Formula>
            <Formula>Q↑corr = sat( mean|r| events / mean|r| all )                {/* normalised correction magnitude */}</Formula>
            <Formula>R↑topic = fraction of events within ±2d of another event     {/* repeated / clustered problems */}</Formula>
            <Formula>S↑file = (n_events/10) · (0.35 + 0.65·Q↑corr) · (1 + 1.2·R↑topic) → 0-100</Formula>
            <Formula>p↑bad = σ((S↑file − 52) / 12 + 0.5·R↑topic)                  {/* adverse-event probability proxy */}</Formula>
            <p className="mt-3 text-[12px] leading-5 text-muted">
              Internal-control penalties (material weakness / ineffective controls) have no public crypto equivalent and are
              explicitly <strong>not scored</strong> — shown as <code className="font-mono text-[11px]">null</code>, never faked.
            </p>
          </section>

          <section>
            <H2 id="verdicts">05 · Verdicts &amp; filters</H2>
            <p className="mt-3 text-[14px] leading-6 text-ink/90">Override order (first match wins):</p>
            <ol className="mt-3 space-y-2 text-[13px] leading-6 text-ink/90 list-decimal list-inside">
              <li><strong>Low-quality growth → AVOID.</strong> Price up &gt;5% over 30d while participation (trade count) fell &gt;10% and buy-side share &lt; 0.47. The “DR rising but churn rising, NRR falling” rejection from the slides.</li>
              <li><strong>Billing distortion → NO SIGNAL.</strong> Reported USD volume +5% or more, but organic unit volume ≤ 0. The demand is price/mix, not adoption.</li>
              <li><strong>Red-flag exclusion → AVOID.</strong> Instability ≥ 55 or p(adverse) ≥ 65% — removed from the long universe, per the slides’ “long-avoidance rule”.</li>
              <li><strong>LONG.</strong> Signal ≥ 55, p(accel) ≥ 60%, 0 &lt; organic growth ≤ 90% (blow-off cap), and price above VWAP-20 — the slides’ entry rule with perpetual-style confirmation.</li>
              <li><strong>WATCH.</strong> Signal ≥ 38 and p(accel) ≥ 50%, but at least one long criterion unmet.</li>
              <li><strong>Otherwise NEUTRAL.</strong></li>
            </ol>
            <p className="mt-3 text-[12px] leading-5 text-muted">
              Exit triggers (for existing positions) mirror the slides: organic growth negative, flow stability &lt; 0.40, or participation exodus &gt; 25%.
            </p>
          </section>

          <section>
            <H2 id="mapping">06 · Slide → crypto mapping</H2>
            <div className="mt-3 overflow-x-auto card">
              <table className="data-table min-w-[760px] text-[12px]">
                <thead>
                  <tr><th>Slide formula (equities)</th><th>Meridian analogue (crypto)</th></tr>
                </thead>
                <tbody>
                  {[
                    ["Deferred revenue DR (current + long-term)", "Unit (base-asset) volume — real demand accumulating"],
                    ["Reported DR growth G↑DR", "Quote (USD) volume growth G↑qv — headline demand"],
                    ["Organic DR growth G↑org (strip M&A, FX, billing)", "Unit volume growth — price/mix distortion stripped"],
                    ["DR acceleration A↑DR", "Organic growth vs. prior 30d window"],
                    ["Deferred-revenue lead D↑lead", "Organic demand growth − price growth (identical form)"],
                    ["Churn stability S↑churn", "Participation (trade count) trend stability, decay-penalised"],
                    ["Retention quality Q↑ret = α·NRR + β·GRR − γ·Churn", "NRR* = 1+organic growth; GRR* = buy-dominant days; churn = participation exodus"],
                    ["Cash conversion C↑cash = OCF / Revenue", "Taker-buy share of unit volume (real converted buying)"],
                    ["Quality Q↑DR = (G↑org)₊ · S · (1+Q)₊ · (1+C)₊", "Same structure, factors saturated and mapped to 0–100"],
                    ["Expected recognition R̂↑DR = DR · Ĉ↑DR", "R̂ = (1 + G↑org) × C↑cash — convertible demand multiple"],
                    ["Gap GAP↑DR = (R̂ − Revenue) / Revenue", "GAP = D↑lead / (1 + G↑rev) — under-recognition vs price"],
                    ["Signal S↑DR (multiplicative)", "Weighted geometric mean of the four sat() factors"],
                    ["Adverse probability P↑bad (restatement classifier)", "Heuristic σ() over instability features — uncalibrated"],
                    ["Amendment frequency F↑amend (10-Q/A …)", "Abnormal-move days per 30 (detrended, 2.5σ)"],
                    ["Filing-instability S↑file", "Event frequency × materiality × clustering → 0–100"],
                    ["Long-avoidance rule / exclusion", "Instability ≥ 55 or p(adverse) ≥ 65% ⇒ excluded"],
                    ["VWAP confirmation P_t > VWAP", "VWAP-20 on daily typical price (typical = (H+L+C)/3)"],
                    ["Internal-control penalty I↑IC", "Not publicly observable — unscored (null)"],
                  ].map(([a, b]) => (
                    <tr key={a}>
                      <td className="text-muted align-top">{a}</td>
                      <td className="text-ink">{b}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <H2 id="limits">07 · Limitations — read this first</H2>
            <ul className="mt-3 space-y-2 text-[13px] leading-6 text-ink/90">
              <li className="flex items-start gap-2"><ShieldAlert size={15} strokeWidth={2} className="mt-1 shrink-0 text-warn" />Probabilities p(accel) / p(adverse) are <strong>sigmoid blends, not calibrated classifiers</strong>. No backtest ships with this repo; treat them as ordering scores, not odds.</li>
              <li className="flex items-start gap-2"><ShieldAlert size={15} strokeWidth={2} className="mt-1 shrink-0 text-warn" />Taker-buy volume measures aggressor-side flow, not intent — market orders can be bots, hedges or wash-like behaviour.</li>
              <li className="flex items-start gap-2"><ShieldAlert size={15} strokeWidth={2} className="mt-1 shrink-0 text-warn" />Spot OHLCV knows nothing about derivatives positioning, unlock schedules, treasury flows or audits. A “LONG” here can still rug.</li>
              <li className="flex items-start gap-2"><ShieldAlert size={15} strokeWidth={2} className="mt-1 shrink-0 text-warn" />Cross-exchange fragmentation: Binance volume ≠ global volume. Thin listings can print organic-looking curves.</li>
              <li className="flex items-start gap-2"><ShieldAlert size={15} strokeWidth={2} className="mt-1 shrink-0 text-warn" />All thresholds (55 / 60% / 90% cap / 2.5σ) are judgement defaults, exposed as constants — not fitted to a labelled history. Backtest before sizing real capital.</li>
            </ul>
            <p className="mt-4 text-[12px] text-muted flex items-center gap-2"><FlaskConical size={14} strokeWidth={2} /> Educational research tool. Not financial advice. Data: Binance / MEXC / CoinGecko public APIs.</p>
            <div className="mt-6">
              <Link to="/" className="btn-primary focus-ring">Run the screen</Link>
            </div>
          </section>
        </article>
      </div>
    </div>
  );
}
