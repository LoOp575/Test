import React from "react";
import ReactMarkdown from "react-markdown";
import { Brain, AlertCircle, Cpu } from "lucide-react";

function SourceBadge({ source, model }) {
  if (source === "aixchia") {
    return (
      <span className="badge-base badge-info">
        <Cpu size={11} strokeWidth={1.5} /> AIXCHIA · {model}
      </span>
    );
  }
  if (source === "emergent-llm") {
    return (
      <span className="badge-base badge-success">
        <Brain size={11} strokeWidth={1.5} /> Claude Sonnet 4.6
      </span>
    );
  }
  return (
    <span className="badge-base badge-neutral">
      <Cpu size={11} strokeWidth={1.5} /> Rule-based · local
    </span>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3" data-testid="agent-loading">
      <div className="h-3 w-2/3 skeleton rounded-sm" />
      <div className="h-3 w-full skeleton rounded-sm" />
      <div className="h-3 w-5/6 skeleton rounded-sm" />
      <div className="h-3 w-1/2 skeleton rounded-sm mt-4" />
      <div className="h-3 w-3/4 skeleton rounded-sm" />
      <div className="h-3 w-full skeleton rounded-sm" />
    </div>
  );
}

export default function AgentAnalysisPanel({ agent, loading, error }) {
  return (
    <div className="panel p-5 sm:p-6" data-testid="agent-analysis-panel">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <div className="data-label mb-1">// AI Agent Analysis</div>
          <h3 className="font-display text-lg tracking-tight text-ink-50">
            Narrative reading of the quant payload
          </h3>
        </div>
        {agent?.source && (
          <SourceBadge source={agent.source} model={agent.model} />
        )}
      </div>

      {loading && (
        <div className="space-y-3" data-testid="agent-loading-state">
          <div className="flex items-center gap-2 text-xs text-ink-300">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse-dot" />
            Agent membaca payload Monte Carlo & exhaustion...
          </div>
          <LoadingSkeleton />
        </div>
      )}

      {error && !loading && (
        <div className="flex items-start gap-2 text-xs text-rose-400 bg-rose-400/5 border border-rose-400/20 rounded-sm p-3">
          <AlertCircle size={14} strokeWidth={1.5} className="flex-shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {!loading && !error && agent?.warning && (
        <div className="mb-3 flex items-start gap-2 text-xs text-amber-400 bg-amber-400/5 border border-amber-400/20 rounded-sm p-3">
          <AlertCircle size={14} strokeWidth={1.5} className="flex-shrink-0 mt-0.5" />
          <span>{agent.warning}</span>
        </div>
      )}

      {!loading && agent?.analysis && (
        <div className="prose-agent" data-testid="agent-content">
          <ReactMarkdown>{agent.analysis}</ReactMarkdown>
        </div>
      )}

      {!loading && !error && !agent && (
        <div className="text-xs text-ink-400 py-4">Agent analysis belum tersedia.</div>
      )}
    </div>
  );
}
