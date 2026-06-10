/* ============================================================
   AgentAnalysisPanel — displays AI agent narrative analysis
   ============================================================ */

export default function AgentAnalysisPanel({ agent, loading, error }) {
  return (
    <div className="card agent-card animate-in">
      <div className="card-title">AI Agent Analysis</div>

      {loading && (
        <div className="agent-loading">
          <span className="agent-dot" />
          Agent sedang membaca Monte Carlo, TP/SL, dan pump exhaustion...
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}

      {!loading && !error && !agent && (
        <p className="agent-empty">Agent analysis belum tersedia.</p>
      )}

      {agent?.warning && (
        <div className="agent-warning">
          {agent.warning}
        </div>
      )}

      {agent?.analysis && (
        <div className="agent-content">
          <div className="agent-source">
            Source: <strong>{agent.source === 'aixchia' ? 'AIXCHIA Agent' : 'Local Fallback Agent'}</strong>
            {agent.model ? <span> · {agent.model}</span> : null}
          </div>
          <pre>{agent.analysis}</pre>
        </div>
      )}
    </div>
  );
}
