import React from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Activity, Github, ShieldAlert } from "lucide-react";

export default function Layout() {
  const location = useLocation();
  return (
    <div className="min-h-screen bg-ink-900 bg-grain bg-glow text-ink-100 relative">
      <header
        className="sticky top-0 z-30 backdrop-blur-xl bg-ink-900/80 border-b border-ink-600"
        data-testid="site-header"
      >
        <div className="max-w-[1480px] mx-auto px-4 sm:px-6 lg:px-8 py-3.5 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3 group" data-testid="brand-link">
            <div className="w-8 h-8 grid place-items-center border border-ink-500 rounded-sm bg-ink-800 group-hover:border-emerald-400 transition-colors">
              <Activity size={16} strokeWidth={1.75} className="text-emerald-400" />
            </div>
            <div className="flex flex-col leading-none">
              <span className="font-display text-lg font-medium tracking-tight text-ink-50">
                LQ-Short <span className="text-emerald-400">Hunter</span>
              </span>
              <span className="font-mono text-[9px] uppercase tracking-wider3 text-ink-300 mt-0.5">
                Probabilistic Short Engine · v2.0
              </span>
            </div>
          </Link>

          <nav className="flex items-center gap-2">
            <Link
              to="/"
              data-testid="nav-screener"
              className={
                "tab-btn " + (location.pathname === "/" ? "tab-btn-active" : "")
              }
            >
              Screener
            </Link>
            <a
              href="https://github.com/LoOp575/Test"
              target="_blank"
              rel="noreferrer"
              className="btn-ghost"
              data-testid="nav-github"
            >
              <Github size={14} strokeWidth={1.5} />
              <span className="hidden sm:inline">GitHub</span>
            </a>
          </nav>
        </div>
      </header>

      <main className="relative z-10 max-w-[1480px] mx-auto px-4 sm:px-6 lg:px-8 py-6 lg:py-10">
        <Outlet />
      </main>

      <footer className="relative z-10 max-w-[1480px] mx-auto px-4 sm:px-6 lg:px-8 pb-12 pt-4">
        <div className="panel p-4 flex items-start gap-3" data-testid="footer-disclaimer">
          <ShieldAlert size={16} strokeWidth={1.5} className="text-amber-400 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-ink-300 leading-relaxed">
            <span className="text-amber-400 font-semibold uppercase tracking-wider2">Disclaimer · </span>
            Educational &amp; research purposes only. <span className="text-ink-200">NOT</span> financial advice,
            trading signals, or investment recommendations. Monte Carlo simulations are stochastic and do not
            predict future prices. Use at your own risk.
          </p>
        </div>
      </footer>
    </div>
  );
}
