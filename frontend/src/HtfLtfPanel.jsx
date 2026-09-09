import { useEffect, useState } from "react";
import { fetchSymbols, runBacktest } from "./api";
import TradeReplay from "./TradeReplay";
import { Metric, EquityCurve, SymbolPicker, CapitalInput, fmtMoney, tradeMoney } from "./Shared";

export default function HtfLtfPanel() {
  const [symbolsList, setSymbolsList] = useState([]);
  const [htfToLtf, setHtfToLtf] = useState({});
  const [symbols, setSymbols] = useState(["EURUSD", "USDJPY", "XAUUSD"]);
  const [htf, setHtf] = useState("H4");
  const [htfBars, setHtfBars] = useState(3000);
  const [minRr, setMinRr] = useState(3.0);
  const [maxRr, setMaxRr] = useState(5.0);
  const [riskPct, setRiskPct] = useState(1.0);
  const [startEquity, setStartEquity] = useState(10000);
  const [london, setLondon] = useState([7, 10]);
  const [ny, setNy] = useState([12, 15]);
  const [pyramiding, setPyramiding] = useState(false);
  const [maxLegs, setMaxLegs] = useState(3);
  const [pyramidRiskMult, setPyramidRiskMult] = useState(0.5);

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [selectedTradeId, setSelectedTradeId] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);

  useEffect(() => {
    fetchSymbols().then((d) => {
      setSymbolsList(d.symbols);
      setHtfToLtf(d.htf_to_ltf);
    });
  }, []);

  function toggleSymbol(sym) {
    setSymbols((s) => (s.includes(sym) ? s.filter((x) => x !== sym) : [...s, sym]));
  }

  async function handleRun() {
    setLoading(true);
    setErrorMsg(null);
    setSelectedTradeId(null);
    try {
      const data = await runBacktest({
        symbols, htf, htf_bars: htfBars, min_rr: minRr, max_rr: maxRr,
        risk_pct: riskPct, start_equity: startEquity, london, ny, pyramiding,
        max_pyramid_legs: maxLegs, pyramid_risk_mult: pyramidRiskMult,
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

        <label>HTF</label>
        <select value={htf} onChange={(e) => setHtf(e.target.value)}>
          {Object.keys(htfToLtf).map((h) => (
            <option key={h} value={h}>{h}</option>
          ))}
        </select>
        <div className="hint">LTF used: {htfToLtf[htf]}</div>

        <label>HTF bars (history depth): {htfBars}</label>
        <input type="range" min={500} max={9000} step={500} value={htfBars}
               onChange={(e) => setHtfBars(Number(e.target.value))} />

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

        <h3>Killzones (UTC hours)</h3>
        <label>London: {london[0]}–{london[1]}</label>
        <div className="range-pair">
          <input type="number" min={0} max={23} value={london[0]}
                 onChange={(e) => setLondon([Number(e.target.value), london[1]])} />
          <input type="number" min={0} max={23} value={london[1]}
                 onChange={(e) => setLondon([london[0], Number(e.target.value)])} />
        </div>
        <label>New York: {ny[0]}–{ny[1]}</label>
        <div className="range-pair">
          <input type="number" min={0} max={23} value={ny[0]}
                 onChange={(e) => setNy([Number(e.target.value), ny[1]])} />
          <input type="number" min={0} max={23} value={ny[1]}
                 onChange={(e) => setNy([ny[0], Number(e.target.value)])} />
        </div>

        <h3>Pyramiding</h3>
        <label className="checkbox-row">
          <input type="checkbox" checked={pyramiding} onChange={(e) => setPyramiding(e.target.checked)} />
          Add on BOS + fresh FVG continuation zone
        </label>
        <p className="hint">
          After the base entry, watch the LTF for a break of structure that leaves behind a fresh
          same-direction FVG. When price retraces into that zone, add a reduced-risk leg.
        </p>
        <label>Max legs (base + adds): {maxLegs}</label>
        <input type="range" min={1} max={5} value={maxLegs} disabled={!pyramiding}
               onChange={(e) => setMaxLegs(Number(e.target.value))} />
        <label>Risk multiplier per added leg: {pyramidRiskMult.toFixed(1)}</label>
        <input type="range" min={0.1} max={1} step={0.1} value={pyramidRiskMult} disabled={!pyramiding}
               onChange={(e) => setPyramidRiskMult(Number(e.target.value))} />

        <button className="run-btn" onClick={handleRun} disabled={loading}>
          {loading ? "Running…" : "Run backtest"}
        </button>
        {errorMsg && <div className="error">{errorMsg}</div>}
      </aside>

      <main className="main">
        <h1>ICT / SMC — HTF bias → LTF confirmation backtest</h1>
        <p className="subtitle">
          HTF POI touch → LTF liquidity sweep → MSS/CISD → OTE pullback → entry, RR-based target,
          London/NY killzones only. Data pulled live from your MT5 terminal (read-only).
        </p>

        {!result && <div className="info-box">Configure settings and click Run backtest.</div>}

        {result && result.trades.length === 0 && (
          <div className="info-box">No trades passed confirmation with these settings.</div>
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
                    <th></th><th>Symbol</th><th>Dir</th><th>Leg</th>
                    <th>Entry time</th><th>Outcome</th><th>R</th><th>Risked</th><th>{"P&L"}</th>
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
                      <td>{t.leg_type}</td>
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
