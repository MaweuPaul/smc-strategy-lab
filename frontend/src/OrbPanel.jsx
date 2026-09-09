import { useEffect, useState } from "react";
import { fetchSymbols, runOrbBacktest } from "./api";
import TradeReplay from "./TradeReplay";
import { Metric, EquityCurve, SymbolPicker, CapitalInput, fmtMoney, tradeMoney } from "./Shared";

export default function OrbPanel() {
  const [symbolsList, setSymbolsList] = useState([]);
  const [symbols, setSymbols] = useState(["EURUSD", "USDJPY", "XAUUSD"]);
  const [bars, setBars] = useState(1_800_000);
  const [openHour, setOpenHour] = useState(7);
  const [openMinute, setOpenMinute] = useState(0);
  const [rangeMinutes, setRangeMinutes] = useState(30);
  const [targetMode, setTargetMode] = useState("measured_move");
  const [targetRangeMult, setTargetRangeMult] = useState(1.0);
  const [minRr, setMinRr] = useState(1.5);
  const [maxRr, setMaxRr] = useState(3.0);
  const [useDailyBias, setUseDailyBias] = useState(true);
  const [riskPct, setRiskPct] = useState(1.0);
  const [startEquity, setStartEquity] = useState(10000);

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [selectedTradeId, setSelectedTradeId] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);

  useEffect(() => {
    fetchSymbols().then((d) => setSymbolsList(d.symbols));
  }, []);

  function toggleSymbol(sym) {
    setSymbols((s) => (s.includes(sym) ? s.filter((x) => x !== sym) : [...s, sym]));
  }

  async function handleRun() {
    setLoading(true);
    setErrorMsg(null);
    setSelectedTradeId(null);
    try {
      const data = await runOrbBacktest({
        symbols, bars, session_open_hour: openHour, session_open_minute: openMinute,
        range_minutes: rangeMinutes, target_mode: targetMode, target_range_mult: targetRangeMult,
        min_rr: minRr, max_rr: maxRr, use_daily_bias: useDailyBias,
        risk_pct: riskPct, start_equity: startEquity,
      });
      setResult(data);
      if (data.trades.length > 0) setSelectedTradeId(data.trades[0].id);
    } catch (e) {
      setErrorMsg(e?.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  }

  const selectedTrade = result?.trades.find((t) => t.id === selectedTradeId) || null;

  return (
    <div className="app">
      <aside className="sidebar">
        <h2>Settings</h2>

        <label>Symbols</label>
        <SymbolPicker symbolsList={symbolsList} symbols={symbols} toggleSymbol={toggleSymbol} />

        <label>History depth (M1 bars): {bars.toLocaleString()}</label>
        <input type="range" min={100000} max={1900000} step={100000} value={bars}
               onChange={(e) => setBars(Number(e.target.value))} />

        <h3>Opening range</h3>
        <label>Session open (UTC)</label>
        <div className="range-pair">
          <input type="number" min={0} max={23} value={openHour}
                 onChange={(e) => setOpenHour(Number(e.target.value))} />
          <input type="number" min={0} max={59} value={openMinute}
                 onChange={(e) => setOpenMinute(Number(e.target.value))} />
        </div>
        <label>Range length (minutes): {rangeMinutes}</label>
        <input type="range" min={1} max={60} value={rangeMinutes}
               onChange={(e) => setRangeMinutes(Number(e.target.value))} />
        <p className="hint">3-minute ranges are very noisy (tested: -74% drawdown). 30 minutes tested best
          (+0.03 avg R, -22.5% drawdown) — that's the default here.</p>

        <h3>Target</h3>
        <div className="mode-filter">
          {[
            { key: "measured_move", label: "Measured move (OR width x)" },
            { key: "rr", label: "Risk:Reward" },
          ].map((opt) => (
            <label key={opt.key} className="radio-row">
              <input type="radio" name="targetMode" checked={targetMode === opt.key}
                     onChange={() => setTargetMode(opt.key)} />
              {opt.label}
            </label>
          ))}
        </div>
        {targetMode === "measured_move" ? (
          <>
            <label>Target = OR width x: {targetRangeMult.toFixed(1)}</label>
            <input type="range" min={0.5} max={4} step={0.5} value={targetRangeMult}
                   onChange={(e) => setTargetRangeMult(Number(e.target.value))} />
          </>
        ) : (
          <>
            <label>Min RR: {minRr.toFixed(1)}</label>
            <input type="range" min={0.5} max={5} step={0.5} value={minRr}
                   onChange={(e) => setMinRr(Number(e.target.value))} />
            <label>Max RR: {maxRr.toFixed(1)}</label>
            <input type="range" min={0.5} max={8} step={0.5} value={maxRr}
                   onChange={(e) => setMaxRr(Number(e.target.value))} />
          </>
        )}

        <h3>Filters</h3>
        <label className="checkbox-row">
          <input type="checkbox" checked={useDailyBias} onChange={(e) => setUseDailyBias(e.target.checked)} />
          Daily bias filter (today's OR midpoint vs. yesterday's)
        </label>

        <label>Risk % per trade: {riskPct.toFixed(2)}</label>
        <input type="range" min={0.25} max={5} step={0.25} value={riskPct}
               onChange={(e) => setRiskPct(Number(e.target.value))} />

        <CapitalInput value={startEquity} onChange={setStartEquity} />

        <button className="run-btn" onClick={handleRun} disabled={loading}>
          {loading ? "Running… (M1 data, can take a while)" : "Run backtest"}
        </button>
        {errorMsg && <div className="error">{errorMsg}</div>}
      </aside>

      <main className="main">
        <h1>Opening Range Breakout (ORB)</h1>
        <p className="subtitle">
          Marks the high/low of the first N minutes after a session open, then trades the first
          M1 close beyond either side. Stop at the opposite side of the range; target is either a
          multiple of the range's own width (measured move) or RR-based. One trade per day.
          Data pulled live from your MT5 terminal (read-only).
        </p>

        {!result && <div className="info-box">Configure settings and click Run backtest. M1 history pulls can take a minute or two per symbol.</div>}

        {result && result.trades.length === 0 && (
          <div className="info-box">No trades generated with these settings.</div>
        )}

        {result && result.metrics && (
          <>
            <div className="metrics-row">
              <Metric label="Trades" value={result.metrics.trades} />
              <Metric label="Win rate" value={`${result.metrics.win_rate.toFixed(1)}%`} />
              <Metric label="Avg R" value={result.metrics.avg_r.toFixed(2)} />
              <Metric label="Profit factor" value={result.metrics.profit_factor?.toFixed(2) ?? "inf"} />
              <Metric label="Max drawdown" value={`${result.metrics.max_drawdown_pct.toFixed(1)}%`} />
              <Metric label="Final equity" value={fmtMoney(result.metrics.final_equity)} />
              <Metric label="Initial risk/trade" value={fmtMoney(startEquity * (riskPct / 100))} />
            </div>

            <EquityCurve equity={result.metrics.equity_curve} />

            <h3>Per-symbol breakdown</h3>
            <table className="table">
              <thead>
                <tr><th>Symbol</th><th>Trades</th><th>Win rate</th><th>Avg R</th></tr>
              </thead>
              <tbody>
                {Object.entries(result.per_symbol).map(([sym, m]) => (
                  <tr key={sym}>
                    <td>{sym}</td><td>{m.trades}</td>
                    <td>{m.win_rate.toFixed(1)}%</td><td>{m.avg_r.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h3>Trade log</h3>
            <div className="trade-table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th></th><th>Symbol</th><th>Dir</th><th>Entry time</th>
                    <th>Outcome</th><th>R</th><th>Risked</th><th>{"P&L"}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((t, i) => {
                    const { riskAmount, pnlAmount } = tradeMoney(result.trades, result.metrics.equity_curve, riskPct, i);
                    return (
                    <tr
                      key={t.id}
                      className={t.id === selectedTradeId ? "row-selected" : ""}
                      onClick={() => setSelectedTradeId(t.id)}
                    >
                      <td><input type="radio" readOnly checked={t.id === selectedTradeId} /></td>
                      <td>{t.symbol}</td>
                      <td>{t.direction}</td>
                      <td>{t.entry_time?.replace("T", " ").slice(0, 16)}</td>
                      <td>{t.outcome}</td>
                      <td className={t.r_multiple > 0 ? "pos" : "neg"}>{t.r_multiple?.toFixed(2)}</td>
                      <td>{fmtMoney(riskAmount)}</td>
                      <td className={pnlAmount > 0 ? "pos" : "neg"}>{fmtMoney(pnlAmount)}</td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <h3>Candle replay</h3>
            {selectedTrade ? (
              <TradeReplay trade={selectedTrade} ltf={result.ltf} />
            ) : (
              <div className="info-box">Click a row above to replay that trade.</div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
