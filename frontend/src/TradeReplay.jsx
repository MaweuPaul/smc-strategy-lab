import { useEffect, useRef, useState } from "react";
import { createChart, CandlestickSeries, createSeriesMarkers } from "lightweight-charts";
import { fetchCandles } from "./api";

import { eventIndex, TF_SECONDS } from "./dailyObAnalysis";

const TIMEFRAMES = ["M1", "M5", "M15", "H1", "H4", "D1"];

// how much extra context to pad the fetch window with, scaled to the
// timeframe -- a few H1 candles of padding would be nearly nothing on a
// D1 chart, and a few D1-candles'-worth would be an enormous M1 fetch.
const TF_PADDING_MS = {
  M1: 4 * 3600 * 1000,
  M5: 8 * 3600 * 1000,
  M15: 8 * 3600 * 1000,
  H1: 8 * 3600 * 1000,
  H4: 5 * 24 * 3600 * 1000,
  D1: 45 * 24 * 3600 * 1000,
};

export default function TradeReplay({ trade, ltf, auditMode = true }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const priceLinesRef = useRef({});
  const markersApiRef = useRef(null);
  const intervalRef = useRef(null);

  const [timeframe, setTimeframe] = useState(ltf);
  const [candles, setCandles] = useState([]);
  const [revealed, setRevealed] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [playSpeed, setPlaySpeed] = useState(180);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const idxRef = useRef({});

  // trade's own entry timeframe is the sensible default each time a new
  // trade is selected, but don't fight the user's choice on every re-render
  useEffect(() => {
    setTimeframe(trade?.ltf || ltf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trade?.id, trade?.ltf, ltf]);

  useEffect(() => {
    if (!trade) return;
    let active = true;
    idxRef.current = {};
    seriesRef.current?.setData([]);
    setPlaying(false);
    setError(null);
    setLoading(true);
    setCandles([]);
    setRevealed(0);

    const pad = TF_PADDING_MS[timeframe] ?? 8 * 3600 * 1000;
    const windowStart = trade.window_start_time
      ? new Date(trade.window_start_time)
      : new Date(new Date(trade.entry_time).getTime() - 24 * 3600 * 1000);
    const start = new Date(windowStart.getTime() - pad).toISOString();
    const end = new Date(new Date(trade.replay_end_time || new Date(Date.parse(trade.entry_time) + 30 * 86400000).toISOString()).getTime() + pad).toISOString();

    fetchCandles(trade.symbol, timeframe, start, end)
      .then((cs) => {
        if (!active) return;
        setCandles(cs);
        idxRef.current = {
          sweep: eventIndex(cs, trade.touch_time || trade.sweep_time, timeframe),
          mss: eventIndex(cs, trade.signal_confirmed_time || trade.mss_time, timeframe, !!trade.signal_confirmed_time),
          entry: eventIndex(cs, trade.entry_time, timeframe),
          exit: eventIndex(cs, trade.exit_time, timeframe, trade.exit_phase === "close"),
        };
        setRevealed(1);
      })
      .catch((e) => { if (active) setError(e.response?.data?.detail || e.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trade, timeframe]);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 520,
      layout: { background: { color: "transparent" }, textColor: "#ccc" },
      grid: { vertLines: { color: "#222" }, horzLines: { color: "#222" } },
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#26a69a", downColor: "#ef5350",
      borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    });
    chartRef.current = chart;
    seriesRef.current = series;
    markersApiRef.current = createSeriesMarkers(series, []);

    const onResize = () => chart.applyOptions({ width: containerRef.current.clientWidth });
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
    };
  }, []);

  // reset price lines/markers when trade changes
  useEffect(() => {
    if (seriesRef.current) {
      Object.values(priceLinesRef.current).forEach((pl) => seriesRef.current.removePriceLine(pl));
      priceLinesRef.current = {};
      markersApiRef.current?.setMarkers([]);
    }
  }, [trade, timeframe]);

  useEffect(() => {
    if (!seriesRef.current || candles.length === 0 || revealed === 0) return;
    const visible = candles.slice(0, revealed);
    seriesRef.current.setData(visible.map((c) => ({ ...c, time: c.time })));

    const idx = idxRef.current;
    const currentIdx = revealed - 1;
    const clock = candles[currentIdx].time + TF_SECONDS[timeframe];
    const known = time => !!time && Date.parse(time) / 1000 <= clock;
    const lines = priceLinesRef.current;

    const ensureLine = (key, price, color, title) => {
      if (price == null) return;
      if (!lines[key]) {
        lines[key] = seriesRef.current.createPriceLine({
          price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title,
        });
      }
    };
    const ensureRemoved = (key) => {
      if (lines[key]) {
        seriesRef.current.removePriceLine(lines[key]);
        delete lines[key];
      }
    };

    // Reveal an OB only when it was qualified, never at its source candle.
    if (known(trade.ob_available_time || trade.window_start_time)) {
      ensureLine("poi_top", trade.poi_top, "#5b8def", "Confirmed POI top");
      ensureLine("poi_bottom", trade.poi_bottom, "#5b8def", "Confirmed POI bottom");
    } else {
      ensureRemoved("poi_top"); ensureRemoved("poi_bottom");
    }
    if (known(trade.fvg_confirmed_time)) {
      ensureLine("fvg_top", trade.fvg_top, "#a080d0", "FVG top");
      ensureLine("fvg_bottom", trade.fvg_bottom, "#a080d0", "FVG bottom");
    } else {
      ensureRemoved("fvg_top"); ensureRemoved("fvg_bottom");
    }

    // OTE zone -- once MSS happened
    if (idx.mss != null && currentIdx >= idx.mss) {
      ensureLine("ote_top", trade.ote_top, "#e08a00", "OTE top");
      ensureLine("ote_bottom", trade.ote_bottom, "#e08a00", "OTE bottom");
    } else {
      ensureRemoved("ote_top");
      ensureRemoved("ote_bottom");
    }

    // entry/stop/target -- once entered
    if (idx.entry != null && currentIdx >= idx.entry && known(trade.entry_time)) {
      ensureLine("entry", trade.entry, "#ffffff", "Entry");
      const changes = (trade.stop_changes || []).filter(change => known(change.time));
      const stop = changes.length ? changes.at(-1).stop : trade.stop;
      ensureRemoved("stop");
      ensureLine("stop", stop, "#ef5350", "Stop");
      ensureLine("target", trade.target, "#26a69a", "Target");
    } else {
      ensureRemoved("entry");
      ensureRemoved("stop");
      ensureRemoved("target");
    }

    // markers: sweep, entry, exit -- appear once reached
    const markers = [];
    if (idx.sweep != null && currentIdx >= idx.sweep) {
      markers.push({
        time: candles[idx.sweep].time,
        position: trade.direction === "long" ? "belowBar" : "aboveBar",
        color: "#b968f2", shape: trade.direction === "long" ? "arrowUp" : "arrowDown",
        text: trade.touch_time ? "OB touch" : "sweep (TS)",
      });
    }
    if (idx.entry != null && currentIdx >= idx.entry && known(trade.entry_time)) {
      markers.push({
        time: candles[idx.entry].time,
        position: trade.direction === "long" ? "belowBar" : "aboveBar",
        color: "#ffffff", shape: trade.direction === "long" ? "arrowUp" : "arrowDown",
        text: "Entry",
      });
    }
    if (idx.exit != null && currentIdx >= idx.exit && known(trade.exit_time)) {
      markers.push({
        time: candles[idx.exit].time,
        position: trade.direction === "long" ? "aboveBar" : "belowBar",
        color: trade.outcome === "win" ? "#26a69a" : "#ef5350",
        shape: "circle",
        text: `Exit (${trade.exit_reason || trade.outcome})`,
      });
    }
    markersApiRef.current?.setMarkers(markers);
  }, [revealed, candles, trade, timeframe]);

  useEffect(() => {
    if (playing) {
      intervalRef.current = setInterval(() => {
        setRevealed((r) => {
          if (r >= candles.length) {
            setPlaying(false);
            return r;
          }
          return r + 1;
        });
      }, playSpeed);
    } else if (intervalRef.current) {
      clearInterval(intervalRef.current);
    }
    return () => clearInterval(intervalRef.current);
  }, [playing, candles.length, playSpeed]);

  function jumpTo(key) {
    const idx = idxRef.current[key];
    if (idx != null) setRevealed(idx + 1);
  }

  if (!trade) return null;
  const clock = candles[revealed - 1] ? candles[revealed - 1].time + TF_SECONDS[timeframe] : -Infinity;
  const showOutcome = !auditMode || clock >= Date.parse(trade.exit_time) / 1000;

  return (
    <div className="replay">
      <div className="replay-header">
        <strong>
          {trade.symbol} {timeframe} — {trade.direction} {showOutcome ? `(${trade.outcome}, ${trade.r_multiple?.toFixed(2)}R)` : "(outcome hidden)"}
        </strong>
        {loading && <span> loading candles…</span>}
        {error && <span className="error"> {error}</span>}
      </div>

      <p className="hint">Completed-bar review: each step reveals a full candle through its close.
        Levels appear only once confirmed. Intrabar exit ordering is modeled, not reconstructed from ticks.</p>
      <div className="replay-timeframes">
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            className={"chip" + (tf === timeframe ? " chip-active" : "")}
            onClick={() => setTimeframe(tf)}
          >
            {tf}
          </button>
        ))}
      </div>

      <div ref={containerRef} className="replay-chart" />

      <div className="replay-jumps">
        <button onClick={() => jumpTo("sweep")} disabled={idxRef.current.sweep == null}>
          Jump to touch/sweep
        </button>
        <button onClick={() => jumpTo("entry")} disabled={idxRef.current.entry == null}>
          Jump to entry
        </button>
        <button onClick={() => jumpTo("exit")} disabled={idxRef.current.exit == null || (auditMode && !showOutcome)}>
          Jump to exit
        </button>
      </div>

      <div className="replay-controls">
        <button onClick={() => setPlaying((p) => !p)} disabled={candles.length === 0}>
          {playing ? "Pause" : "Play"}
        </button>
        <button
          onClick={() => setRevealed((r) => Math.max(1, r - 1))}
          disabled={candles.length === 0}
        >
          ◀ Step
        </button>
        <button
          onClick={() => setRevealed((r) => Math.min(candles.length, r + 1))}
          disabled={candles.length === 0}
        >
          Step ▶
        </button>
        <input
          type="range"
          min={1}
          max={Math.max(1, candles.length)}
          value={revealed}
          onChange={(e) => setRevealed(Number(e.target.value))}
          style={{ flex: 1 }}
        />
        <span>
          {revealed}/{candles.length}
        </span>
        <select value={playSpeed} onChange={(e) => setPlaySpeed(Number(e.target.value))}>
          <option value={400}>0.5x</option>
          <option value={180}>1x</option>
          <option value={80}>2x</option>
          <option value={30}>4x</option>
        </select>
      </div>
    </div>
  );
}
