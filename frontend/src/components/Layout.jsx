import React from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Github } from "lucide-react";

function Wordmark() {
  return (
    <Link to="/" className="flex items-center gap-3 group focus-ring rounded-lg">
      <span
        aria-hidden="true"
        className="grid place-items-center w-9 h-9 rounded-xl bg-pine-800 transition-colors group-hover:bg-pine-700"
      >
        <span className="block w-3.5 h-3.5 rotate-45 border-2 border-gold-400 rounded-[3px]" />
      </span>
      <span className="leading-tight">
        <span className="block font-display text-[19px] font-semibold tracking-tight text-ink">
          Meridian
        </span>
        <span className="block font-mono text-[9px] uppercase tracking-wide2 text-muted">
          Crypto Quality Screener
        </span>
      </span>
    </Link>
  );
}

const NAV = [
  { to: "/", label: "Screener" },
  { to: "/methodology", label: "Methodology" },
];

export default function Layout() {
  const location = useLocation();

  return (
    <div className="min-h-screen bg-paper text-ink flex flex-col">
      {/* ---------- top bar ---------- */}
      <header className="sticky top-0 z-40 backdrop-blur bg-paper/90 border-b border-line">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
          <Wordmark />
          <nav className="flex items-center gap-1 sm:gap-2">
            {NAV.map((item) => {
              const active =
                item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to);
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className={`focus-ring rounded-full px-3.5 sm:px-4 py-2 text-[13px] font-medium transition-colors ${
                    active ? "bg-pine-800 text-paper" : "text-muted hover:text-ink hover:bg-pine-50"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
            <a
              href="https://github.com/LoOp575/Test"
              target="_blank"
              rel="noreferrer"
              aria-label="Source repository"
              className="focus-ring rounded-full p-2 text-muted hover:text-ink hover:bg-pine-50 transition-colors"
            >
              <Github size={17} strokeWidth={1.75} />
            </a>
          </nav>
        </div>
      </header>

      {/* ---------- page body ---------- */}
      <main className="flex-1">
        <Outlet />
      </main>

      {/* ---------- footer ---------- */}
      <footer className="border-t border-line bg-paper">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 py-6 flex flex-col md:flex-row md:items-center md:justify-between gap-3">
          <p className="font-mono text-[10px] uppercase tracking-wide2 text-faint">
            Meridian · Deferred-Demand Quality Model · Daily OHLCV
          </p>
          <p className="text-[11px] leading-5 text-muted max-w-xl">
            Research tool, not financial advice. Scores are heuristic estimates derived from public
            market data; they can be wrong. Always verify liquidity, venue risk and tokenomics
            before acting.
          </p>
        </div>
      </footer>
    </div>
  );
}
