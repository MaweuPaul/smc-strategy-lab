import { useEffect, useRef, useState } from "react";
import { createChart, CandlestickSeries, createSeriesMarkers, CrosshairMode } from "lightweight-charts";

// Chart for the daily FVG retracement strategy: D1 (or M15) candles plus,
// for the selected trade, the FVG zone (top/bottom), entry/stop/target
// lines, and entry/exit markers.
export default function DailyFvgReplay({ candles, trade, decimals, symbol, timeframe }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const markersApiRef = useRef(null);
  const priceLinesRef = useRef([]);
  const tradeRef = useRef(null);
  const candlesRef = useRef(null);
  const [hoverBar, setHoverBar] = useState(null);

  function applyTradeZoom(t) {
    if (!t || !chartRef.current) return;
    const pad = 86400 * 3;
    chartRef.current.timeScale().setVisibleRange({ from: (t.gap_formed_time ?? t.entry_time) - pad, to: t.exit_time + pad });
  }

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 480,
      layout: { background: { color: "transparent" }, textColor: "#9aa4b2", fontSize: 12 },
      grid: { vertLines: { color: "rgba(255,255,255,0.045)" }, horzLines: { color: "rgba(255,255,255,0.045)" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#262b36", scaleMargins: { top: 0.12, bottom: 0.12 } },
      timeScale: { borderColor: "#262b36", timeVisible: true, secondsVisible: false },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#26a69a", downColor: "#ef5350",
      borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    });
    chartRef.current = chart;
    seriesRef.current = series;
    markersApiRef.current = createSeriesMarkers(series, []);

    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !candlesRef.current) { setHoverBar(null); return; }
      const bar = candlesRef.current.find((c) => c.time === param.time);
      setHoverBar(bar ?? null);
    });

    const onResize = () => chart.applyOptions({ width: containerRef.current.clientWidth });
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
    };
  }, []);

  useEffect(() => {
    if (!seriesRef.current || !candles?.length) return;
    candlesRef.current = candles;
    seriesRef.current.setData(candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
    setHoverBar(candles[candles.length - 1]);
    if (tradeRef.current) applyTradeZoom(tradeRef.current);
    else chartRef.current?.timeScale().fitContent();
  }, [candles]);

  useEffect(() => {
    tradeRef.current = trade;
    if (!seriesRef.current) return;
    priceLinesRef.current.forEach((pl) => seriesRef.current.removePriceLine(pl));
    priceLinesRef.current = [];
    if (!trade) return;

    const fmt = (v) => v.toFixed(decimals);
    const win = trade.pnl > 0;
    const addLine = (price, color, title, style = 2) =>
      priceLinesRef.current.push(
        seriesRef.current.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title })
      );

    if (trade.gap_top != null) addLine(trade.gap_top, "#e0a000", `FVG top ${fmt(trade.gap_top)}`);
    if (trade.gap_bottom != null) addLine(trade.gap_bottom, "#e0a000", `FVG bottom ${fmt(trade.gap_bottom)}`);
    addLine(trade.entry_price, "#ffffff", `Entry ${fmt(trade.entry_price)}`, 3);
    addLine(trade.stop, "#ef5350", `SL ${fmt(trade.stop)}`);
    addLine(trade.target, "#26a69a", `Target ${fmt(trade.target)}`);

    const isShort = trade.direction === "short";
    const dirColor = win ? "#26a69a" : "#ef5350";
    const markers = [];
    if (trade.gap_formed_time != null)
      markers.push({ time: trade.gap_formed_time, position: "belowBar", color: "#e0a000", shape: "circle", text: "FVG formed" });
    if (trade.entered_time != null)
      markers.push({ time: trade.entered_time, position: "belowBar", color: "#a080d0", shape: "circle", text: "Entered gap" });
    markers.push(
      isShort
        ? { time: trade.entry_time, position: "aboveBar", color: "#ffffff", shape: "arrowDown", text: "Short" }
        : { time: trade.entry_time, position: "belowBar", color: "#ffffff", shape: "arrowUp", text: "Buy" }
    );
    markers.push({
      time: trade.exit_time, position: "aboveBar", color: dirColor, shape: "circle",
      text: `${win ? "Win" : "Loss"} ${trade.r_multiple >= 0 ? "+" : ""}${trade.r_multiple.toFixed(2)}R`,
    });
    markersApiRef.current?.setMarkers(markers.sort((a, b) => a.time - b.time));

    applyTradeZoom(trade);
  }, [trade, decimals]);

  if (!candles?.length) return <div className="info-box">No candle data.</div>;

  const win = trade && trade.pnl > 0;
  const fmt = (v) => v.toFixed(decimals);

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <div className="chart-card-title">
          <span className="chart-card-symbol">{symbol}</span>
          <span className="chart-card-tf">{timeframe}</span>
        </div>
        {hoverBar && (
          <div className="chart-card-ohlc">
            <span>O <b className={hoverBar.close >= hoverBar.open ? "pos" : "neg"}>{fmt(hoverBar.open)}</b></span>
            <span>H <b className={hoverBar.close >= hoverBar.open ? "pos" : "neg"}>{fmt(hoverBar.high)}</b></span>
            <span>L <b className={hoverBar.close >= hoverBar.open ? "pos" : "neg"}>{fmt(hoverBar.low)}</b></span>
            <span>C <b className={hoverBar.close >= hoverBar.open ? "pos" : "neg"}>{fmt(hoverBar.close)}</b></span>
          </div>
        )}
        {trade && (
          <div className={"chart-card-badge " + (win ? "chart-card-badge-win" : "chart-card-badge-loss")}>
            {trade.direction === "short" ? "Short" : "Long"} · {win ? "Win" : "Loss"}{" "}
            {trade.r_multiple >= 0 ? "+" : ""}{trade.r_multiple.toFixed(2)}R
          </div>
        )}
      </div>
      <p className="hint" style={{ padding: "0 14px" }}>
        Orange dashed lines mark the bullish FVG's top/bottom. Select a trade below to see when the
        gap formed, when price retraced into it, the entry, and the SL/target/exit.
      </p>
      <div ref={containerRef} className="replay-chart" />
      {trade && (
        <div className="chart-card-why">
          <strong>Why this trade was taken</strong>
          <p>{trade.why}</p>
        </div>
      )}
    </div>
  );
}
