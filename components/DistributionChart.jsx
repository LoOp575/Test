/* ============================================================
   DistributionChart — Recharts histogram of simulated prices
   Loaded client-side only using dynamic import in index.jsx
   ============================================================ */

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Cell
} from 'recharts';

const formatCompactPrice = (value) => {
  const num = Number(value) || 0;

  if (Math.abs(num) >= 1_000_000) return (num / 1_000_000).toFixed(1) + 'M';
  if (Math.abs(num) >= 1_000) return (num / 1_000).toFixed(0) + 'K';
  return String(Math.round(num));
};

export default function DistributionChart({ results }) {
  const buckets = results?.chart?.buckets || [];

  if (!results || results.ok === false || buckets.length === 0) return null;

  const currentPrice = results.input?.currentPrice || 0;
  const takeProfit = results.input?.takeProfit || 0;
  const stopLoss = results.input?.stopLoss || 0;
  const simulations = results.input?.simulations || 0;
  const mean = results.stats?.mean || 0;

  const data = buckets.map((bucket) => ({
    price: bucket.mid,
    count: bucket.count,
    lower: bucket.lower,
    upper: bucket.upper,
    below: bucket.mid < currentPrice
  }));

  const TooltipContent = ({ active, payload }) => {
    if (!active || !payload?.length) return null;

    const item = payload[0].payload;
    const percentage = simulations > 0 ? ((item.count / simulations) * 100).toFixed(2) : '0.00';

    return (
      <div
        style={{
          background: 'var(--bg-surface-alt)',
          border: '1px solid var(--border-light)',
          borderRadius: 8,
          padding: '10px 14px',
          fontFamily: 'var(--font-mono)',
          fontSize: '0.72rem',
          lineHeight: 1.6
        }}
      >
        <div style={{ color: 'var(--text-secondary)' }}>
          ${formatCompactPrice(item.lower)} — ${formatCompactPrice(item.upper)}
        </div>
        <div style={{ color: 'var(--accent-cyan)' }}>
          Count: {item.count.toLocaleString('en-US')} ({percentage}%)
        </div>
      </div>
    );
  };

  const prices = data.map((item) => item.price);
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const tickCount = 8;
  const tickStep = (maxPrice - minPrice) / Math.max(tickCount - 1, 1);
  const ticks = Array.from({ length: tickCount }, (_, index) => minPrice + index * tickStep);

  return (
    <div className="chart-card animate-in">
      <div className="card-title">
        Price Distribution — {simulations.toLocaleString('en-US')} Simulations
      </div>

      <ResponsiveContainer width="100%" height={340}>
        <BarChart data={data} margin={{ top: 20, right: 20, left: 10, bottom: 20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.035)" vertical={false} />

          <XAxis dataKey="price" type="number" domain={[minPrice, maxPrice]} ticks={ticks} tickFormatter={formatCompactPrice} stroke="var(--text-muted)" fontSize={10} fontFamily="DM Mono" tickLine={false} />
          <YAxis stroke="var(--text-muted)" fontSize={10} fontFamily="DM Mono" tickLine={false} axisLine={false} />
          <Tooltip content={<TooltipContent />} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />

          <ReferenceLine x={takeProfit} stroke="var(--accent-green)" strokeDasharray="4 4" label={{ value: 'TP', position: 'top', fill: 'var(--accent-green)', fontSize: 10, fontFamily: 'DM Mono' }} />
          <ReferenceLine x={currentPrice} stroke="rgba(255,255,255,0.5)" strokeDasharray="6 4" label={{ value: 'Current', position: 'top', fill: 'rgba(255,255,255,0.5)', fontSize: 10, fontFamily: 'DM Mono' }} />
          <ReferenceLine x={stopLoss} stroke="var(--accent-red)" strokeDasharray="4 4" label={{ value: 'SL', position: 'top', fill: 'var(--accent-red)', fontSize: 10, fontFamily: 'DM Mono' }} />
          <ReferenceLine x={mean} stroke="var(--accent-amber)" strokeDasharray="2 3" label={{ value: 'Mean', position: 'insideTopRight', fill: 'var(--accent-amber)', fontSize: 9, fontFamily: 'DM Mono' }} />

          <Bar dataKey="count" radius={[2, 2, 0, 0]} maxBarSize={18}>
            {data.map((item, index) => (
              <Cell key={index} fill={item.below ? 'rgba(0,229,255,0.55)' : 'rgba(255,45,85,0.30)'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      <div className="chart-legend">
        <span className="chart-legend-item"><span className="chart-legend-dot" style={{ background: 'rgba(0,229,255,0.55)' }} />Below Current</span>
        <span className="chart-legend-item"><span className="chart-legend-dot" style={{ background: 'rgba(255,45,85,0.30)' }} />Above Current</span>
        <span className="chart-legend-item"><span className="chart-legend-dot" style={{ background: 'rgba(255,255,255,0.5)' }} />Current Price</span>
        <span className="chart-legend-item"><span className="chart-legend-dot" style={{ background: 'var(--accent-green)' }} />Take Profit</span>
        <span className="chart-legend-item"><span className="chart-legend-dot" style={{ background: 'var(--accent-red)' }} />Stop Loss</span>
        <span className="chart-legend-item"><span className="chart-legend-dot" style={{ background: 'var(--accent-amber)' }} />Mean</span>
      </div>
    </div>
  );
}
