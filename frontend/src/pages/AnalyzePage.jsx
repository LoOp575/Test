import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, Loader2 } from "lucide-react";
import { apiPost } from "../lib/utils";

import MarketSummaryCard from "../components/MarketSummaryCard";
import PumpExhaustionCard from "../components/PumpExhaustionCard";
import ResultPanel from "../components/ResultPanel";
import AgentAnalysisPanel from "../components/AgentAnalysisPanel";
import DistributionChart from "../components/DistributionChart";

export default function AnalyzePage() {
  const { symbol } = useParams();
  const sym = (symbol || "").toUpperCase();

  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const [agent, setAgent] = useState(null);
  const [agentLoading, setAgentLoading] = useState(false);
  const [agentError, setAgentError] = useState(null);

  useEffect(() => {
    if (!sym) return;
    let cancelled = false;

    async function run() {
      setLoading(true);
      setError(null);
      setData(null);
      setAgent(null);
      setAgentError(null);
      setAgentLoading(false);

      try {
        const j = await apiPost(`/api/analyze/${encodeURIComponent(sym)}`);
        if (cancelled) return;
        setData(j);
        setLoading(false);

        setAgentLoading(true);
        const ag = await apiPost("/api/agent-analysis", {
          market: j.market,
          autoLevels: j.autoLevels,
          results: j.results,
        });
        if (cancelled) return;
        setAgent(ag);
      } catch (e) {
        if (cancelled) return;
        if (data) {
          setAgentError(e.message);
        } else {
          setError(e.message);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
          setAgentLoading(false);
        }
      }
    }

    run();
    return () => {
      cancelled = true;
    };
  }, [sym]);

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Topbar */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <Link to="/" className="btn-ghost" data-testid="back-to-screener">
          <ArrowLeft size={14} strokeWidth={1.5} />
          Back to screener
        </Link>
        <div className="flex items-center gap-3">
          <span className="data-label">Analyzing</span>
          <span
            className="font-mono text-lg font-medium text-ink-50 tracking-tight"
            data-testid="analyze-symbol"
          >
            {sym}
          </span>
        </div>
      </div>

      {loading && (
        <div className="panel p-12 flex items-center justify-center" data-testid="analyze-loading">
          <div className="flex flex-col items-center gap-4">
            <Loader2 size={28} strokeWidth={1.5} className="text-emerald-400 animate-spin" />
            <div>
              <div className="font-display text-base text-ink-50 mb-1 text-center">
                Running automatic analysis
              </div>
              <div className="text-xs text-ink-300 text-center">
                Fetching market · pump exhaustion · 50k Monte Carlo paths
              </div>
            </div>
          </div>
        </div>
      )}

      {error && !data && (
        <div
          className="panel p-6 border-rose-400/30 bg-rose-400/5"
          data-testid="analyze-error"
        >
          <div className="font-display text-rose-400 mb-1">Analysis failed</div>
          <div className="text-sm text-ink-300">{error}</div>
        </div>
      )}

      {data && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
          {/* Left column */}
          <aside className="lg:col-span-4 xl:col-span-3 space-y-5">
            <MarketSummaryCard market={data.market} />
            <PumpExhaustionCard auto={data.autoLevels} />
          </aside>

          {/* Right column */}
          <section className="lg:col-span-8 xl:col-span-9 space-y-5">
            <ResultPanel results={data.results} />
            <AgentAnalysisPanel
              agent={agent}
              loading={agentLoading}
              error={agentError}
            />
            <DistributionChart results={data.results} />
          </section>
        </div>
      )}
    </div>
  );
}
