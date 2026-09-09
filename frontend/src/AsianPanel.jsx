import { useEffect, useState } from "react";
import { fetchSymbols, runAsianBacktest } from "./api";
import TradeReplay from "./TradeReplay";
import { Metric, EquityCurve, SymbolPicker, CapitalInput, fmtMoney, tradeMoney } from "./Shared";
import MonteCarloPanel from "./MonteCarlo";

export default function AsianPanel() {
  const [symbolsList, setSymbolsList] = useState([]);
  const [symbols, setSymbols] = useState(["EURUSD", "USDJPY", "XAUUSD"]);
  const [ltf, setLtf] = useState("M15");
  const [bars, setBars] = useState(20000);
  const [asianStart, setAsianStart] = useState(0);
  const [asianEnd, setAsianEnd] = useState(6);
  const [minRr, setMinRr] = useState(3.0);
  const [maxRr, setMaxRr] = useState(5.0);
  const [riskPct, setRiskPct] = useState(1.0);
  const [startEquity, setStartEquity] = useState(2000);
  const [modeFilter, setModeFilter] = useState("all");
  const [htfBiasFilter, setHtfBiasFilter] = useState("");
  const [allowNeutralBias, setAllowNeutralBias] = useState(true);

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
      const data = await runAsianBacktest({
        symbols, ltf, bars, asian_start: asianStart, asian_end: asianEnd,
        min_rr: minRr, max_rr: maxRr, risk_pct: riskPct,
        start_equity: startEquity, mode_filter: modeFilter,
        htf_bias_filter: htfBiasFilter || null, allow_neutral_bias: allowNeutralBias,
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

        <label>Timeframe</label>
        <select value={ltf} onChange={(e) => setLtf(e.target.value)}>
          {["M1", "M15", "H1"].map((h) => <option key={h} value={h}>{h}</option>)}
        </select>

        <label>History depth (bars): {bars}</label>
        <input type="range" min={2000} max={128000} step={2000} value={bars}
               onChange={(e) => setBars(Number(e.target.value))} />

        <h3>Asian session (UTC hours)</h3>
        <label>Window: {asianStart}:00–{asianEnd}:00</label>
        <div className="range-pair">
          <input type="number" min={0} max={23} value={asianStart}
                 onChange={(e) => setAsianStart(Number(e.target.value))} />
          <input type="number" min={0} max={23} value={asianEnd}
                 onChange={(e) => setAsianEnd(Number(e.target.value))} />
        </div>

        <label>Min RR (structural target): {minRr.toFixed(1)}</label>
        <input type="range" min={1} max={5} step={0.5} value={minRr}
               onChange={(e) => setMinRr(Number(e.target.value))} />

        <label>Max RR cap: {maxRr.toFixed(1)}</label>
        <input type="range" min={1} max={8} step={0.5} value={maxRr}
               onChange={(e) => setMaxRr(Number(e.target.value))} />

        <label>Risk % per trade: {riskPct.toFixed(2)}</label>
        <input type="range" min={0.25} max={5} step={0.25} value={riskPct}
               onChange={(e) => setRiskPct(Number(e.target.value))} />

        <CapitalInput value={startEquity} onChange={setStartEquity} />

        <h3>Trade type</h3>
        <div className="mode-filter">
          {[
            { key: "all", label: "All" },
            { key: "reversal", label: "Reversal only" },
            { key: "continuation", label: "Continuation only" },
          ].map((opt) => (
            <label key={opt.key} className="radio-row">
              <input
                type="radio"
                name="modeFilter"
                checked={modeFilter === opt.key}
                onChange={() => setModeFilter(opt.key)}
              />
              {opt.label}
            </label>
          ))}
        </div>

        <h3>HTF bias filter</h3>
        <label>Require alignment with</label>
        <select value={htfBiasFilter} onChange={(e) => setHtfBiasFilter(e.target.value)}>
          <option value="">Off (no HTF filter)</option>
          <option value="H4">H4 structure</option>
          <option value="D1">D1 structure</option>
        </select>
        <p className="hint">
          Only takes a trade whose direction agrees with the HTF's own trend structure (HH+HL /
          LH+LL) as of the start of that day's Asian session. Note: this tends to filter out some
          of the best reversal trades (a sweep-reversal is often a move against the prevailing
          trend), so it isn't automatically an improvement — compare before assuming so.
        </p>
        <label className="checkbox-row">
          <input type="checkbox" checked={allowNeutralBias} disabled={!htfBiasFilter}
                 onChange={(e) => setAllowNeutralBias(e.target.checked)} />
          Allow trades when HTF bias is still neutral
        </label>

        <button className="run-btn" onClick={handleRun} disabled={loading}>
          {loading ? "Running…" : "Run backtest"}
        </button>
        {errorMsg && <div className="error">{errorMsg}</div>}
      </aside>

      <main className="main">
        <h1>Asian session — sweep/reversal vs. continuation</h1>
        <p className="subtitle">
          Per day: if a minor swing is swept (wick beyond, close back inside) during the Asian
          session, trade the reversal (sweep → MSS → OTE, same as the HTF/LTF engine). If not,
          follow the session's own trend on a break-and-retest of the Asian range. Entries filtered
          to London/NY killzones. Data pulled live from your MT5 terminal (read-only).
        </p>

        {!result && <div className="info-box">Configure settings and click Run backtest.</div>}

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
              <Metric label="CAGR (annualized)" value={result.metrics.cagr_pct != null ? `${result.metrics.cagr_pct.toFixed(1)}%` : "—"} />
              <Metric label="Final equity" value={fmtMoney(result.metrics.final_equity)} />
              <Metric label="Initial risk/trade" value={fmtMoney(startEquity * (riskPct / 100))} />
            </div>
            {result.metrics.years_span != null && (
              <p className="hint">
                Over {result.metrics.years_span.toFixed(1)} years. CAGR is the number to trust when
                comparing configs — with many trades or high risk %, "Final equity" can compound
                into figures no real market could actually hold (percentage-risk compounding has
                no size limit; real trading does).
              </p>
            )}

            <EquityCurve equity={result.metrics.equity_curve} />

            <MonteCarloPanel trades={result.trades} riskPct={riskPct} startEquity={startEquity} />

            <h3>Reversal vs. continuation</h3>
            <table className="table">
              <thead>
                <tr><th>Mode</th><th>Trades</th><th>Win rate</th><th>Avg R</th></tr>
              </thead>
              <tbody>
                {Object.entries(result.per_mode || {}).map(([mode, m]) => (
                  <tr key={mode}>
                    <td>{mode}</td><td>{m.trades}</td>
                    <td>{m.win_rate.toFixed(1)}%</td>
                    <td className={m.avg_r > 0 ? "pos" : "neg"}>{m.avg_r.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

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
                    <th></th><th>Symbol</th><th>Mode</th><th>Dir</th>
                    <th>Entry time</th><th>Outcome</th><th>R</th><th>Risked</th><th>{"P&L"}</th><th>Balance</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((t, i) => {
                    const { riskAmount, pnlAmount, balanceAfter } = tradeMoney(result.trades, result.metrics.equity_curve, riskPct, i);
                    return (
                    <tr
                      key={t.id}
                      className={t.id === selectedTradeId ? "row-selected" : ""}
                      onClick={() => setSelectedTradeId(t.id)}
                    >
                      <td><input type="radio" readOnly checked={t.id === selectedTradeId} /></td>
                      <td>{t.symbol}</td>
                      <td>{t.mode}</td>
                      <td>{t.direction}</td>
                      <td>{t.entry_time?.replace("T", " ").slice(0, 16)}</td>
                      <td>{t.outcome}</td>
                      <td className={t.r_multiple > 0 ? "pos" : "neg"}>{t.r_multiple?.toFixed(2)}</td>
                      <td>{fmtMoney(riskAmount)}</td>
                      <td className={pnlAmount > 0 ? "pos" : "neg"}>{fmtMoney(pnlAmount)}</td>
                      <td>{fmtMoney(balanceAfter)}</td>
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
