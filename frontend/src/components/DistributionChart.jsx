import React from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Cell,
} from "recharts";

function compact(v) {
  const n = Number(v) || 0;
  const a = Math.abs(n);
  if (a >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (a >= 1_000) return (n / 1_000).toFixed(1) + "K";
  if (a < 0.01 && a > 0) return n.toExponential(2);
  if (a < 1) return n.toFixed(4);
  return n.toFixed(2);
}

function ChartTooltip({ active, payload, sims }) {
  if (!active || !payload?.length) return null;
  const it = payload[0].payload;
  const pct = sims ? ((it.count / sims) * 100).toFixed(2) : "0.00";
  return (
    <div className="bg-ink-700 border border-ink-500 rounded-sm px-3 py-2 font-mono text-xs">
      <div className="text-ink-300">
        {compact(it.lower)} — {compact(it.upper)}
      </div>
      <div className="text-emerald-400 mt-1">
        {it.count.toLocaleString()} ({pct}%)
      </div>
    </div>
  );
}

export default function DistributionChart({ results }) {
  const buckets = results?.chart?.buckets || [];
  if (!results || results.ok === false || buckets.length === 0) return null;

  const cur = results.input?.currentPrice || 0;
  const tp = results.input?.takeProfit || 0;
  const sl = results.input?.stopLoss || 0;
  const sims = results.input?.simulations || 0;
  const mean = results.stats?.mean || 0;

  const data = buckets.map((b) => ({
    price: b.mid,
    count: b.count,
    lower: b.lower,
    upper: b.upper,
    below: b.mid < cur,
  }));

  const prices = data.map((d) => d.price);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);
  const tickCount = 8;
  const step = (maxP - minP) / Math.max(tickCount - 1, 1);
  const ticks = Array.from({ length: tickCount }, (_, i) => minP + i * step);

  return (
    <div className="panel p-5 sm:p-6 animate-fade-in" data-testid="distribution-chart">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <div className="data-label mb-1">// Price Distribution</div>
          <h3 className="font-display text-lg tracking-tight text-ink-50">
            {sims.toLocaleString()} Monte Carlo paths · GBM
          </h3>
        </div>
        <Legend />
      </div>

      <div className="h-[340px]">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 16, right: 12, left: 8, bottom: 16 }}>
            <CartesianGrid strokeDasharray="2 4" stroke="#27272A" vertical={false} />
            <XAxis
              dataKey="price"
              type="number"
              domain={[minP, maxP]}
              ticks={ticks}
              tickFormatter={compact}
              stroke="#71717A"
              fontSize={10}
              fontFamily="JetBrains Mono"
              tickLine={false}
              axisLine={{ stroke: "#27272A" }}
            />
            <YAxis
              stroke="#71717A"
              fontSize={10}
              fontFamily="JetBrains Mono"
              tickLine={false}
              axisLine={false}
            />
            <Tooltip content={<ChartTooltip sims={sims} />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />

            <ReferenceLine
              x={tp}
              stroke="#10b981"
              strokeDasharray="4 4"
              label={{ value: "TP", position: "top", fill: "#10b981", fontSize: 10, fontFamily: "JetBrains Mono" }}
            />
            <ReferenceLine
              x={cur}
              stroke="#A1A1AA"
              strokeDasharray="6 4"
              label={{ value: "CURRENT", position: "top", fill: "#A1A1AA", fontSize: 10, fontFamily: "JetBrains Mono" }}
            />
            <ReferenceLine
              x={sl}
              stroke="#f43f5e"
              strokeDasharray="4 4"
              label={{ value: "SL", position: "top", fill: "#f43f5e", fontSize: 10, fontFamily: "JetBrains Mono" }}
            />
            <ReferenceLine
              x={mean}
              stroke="#f59e0b"
              strokeDasharray="2 3"
              label={{ value: "MEAN", position: "insideTopRight", fill: "#f59e0b", fontSize: 9, fontFamily: "JetBrains Mono" }}
            />

            <Bar dataKey="count" radius={[2, 2, 0, 0]} maxBarSize={20}>
              {data.map((d, i) => (
                <Cell key={i} fill={d.below ? "rgba(16,185,129,0.55)" : "rgba(244,63,94,0.35)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Legend() {
  const items = [
    { c: "rgba(16,185,129,0.55)", l: "Below entry" },
    { c: "rgba(244,63,94,0.35)", l: "Above entry" },
    { c: "#10b981", l: "TP" },
    { c: "#A1A1AA", l: "Current" },
    { c: "#f43f5e", l: "SL" },
    { c: "#f59e0b", l: "Mean" },
  ];
  return (
    <div className="flex flex-wrap gap-3">
      {items.map((it) => (
        <div key={it.l} className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-sm" style={{ background: it.c }} />
          <span className="font-mono text-[10px] uppercase tracking-wider2 text-ink-300">
            {it.l}
          </span>
        </div>
      ))}
    </div>
  );
}
