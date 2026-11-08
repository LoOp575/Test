import { useEffect, useRef, useState } from "react";
import { subscribeMiniTickers } from "./liveTicker";

const FLUSH_MS = 500;

/**
 * Subscribe to live miniTicker prices for a set of symbols.
 * Ticks are coalesced and flushed at most every FLUSH_MS to keep React renders cheap.
 *
 * @param {string[]} symbols e.g. ["BTCUSDT", "ETHUSDT"]
 * @returns {{ ticks: Record<string, {price: number, changePercent: number, quoteVolume?: number}>, live: boolean }}
 */
export function useLiveTickers(symbols) {
  // Stable identity for the effect: sorted, de-duplicated symbol list.
  const key = (symbols || [])
    .map((s) => String(s || "").toUpperCase())
    .filter(Boolean)
    .sort()
    .join(",");

  const [ticks, setTicks] = useState({});
  const [live, setLive] = useState(false);
  const pending = useRef({});
  const flushTimer = useRef(null);

  useEffect(() => {
    const list = key ? key.split(",") : [];
    if (list.length === 0) {
      setTicks({});
      setLive(false);
      return undefined;
    }

    const unsubscribe = subscribeMiniTickers(
      list,
      (tick) => {
        pending.current[tick.symbol] = tick;
        if (!flushTimer.current) {
          flushTimer.current = setTimeout(() => {
            flushTimer.current = null;
            const batch = pending.current;
            pending.current = {};
            setTicks((prev) => ({ ...prev, ...batch }));
          }, FLUSH_MS);
        }
      },
      (status) => setLive(status === "live")
    );

    return () => {
      unsubscribe();
      if (flushTimer.current) {
        clearTimeout(flushTimer.current);
        flushTimer.current = null;
      }
      pending.current = {};
      setLive(false);
    };
  }, [key]);

  return { ticks, live };
}
