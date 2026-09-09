import { useEffect, useState } from "react";
import { fetchSymbols, runDailyObBacktest } from "./api";
import TradeReplay from "./TradeReplay";
import { Metric, EquityCurve, SymbolPicker, CapitalInput, fmtMoney, tradeMoney } from "./Shared";
import MonteCarloPanel from "./MonteCarlo";

export default function DailyObPanel() {
  const [symbolsList, setSymbolsList] = useState([]);
  const [symbols, setSymbols] = useState(["EURUSD", "USDJPY", "XAUUSD", "USTEC", "XAGUSD", "US30", "US500"]);
  const [htfTimeframe, setHtfTimeframe] = useState("D1");
  const [htfBars, setHtfBars] = useState(2600);
  const [swingWindow, setSwingWindow] = useState(3);
  const [minRr, setMinRr] = useState(3.0);
  const [maxRr, setMaxRr] = useState(5.0);
  const [useKillzones, setUseKillzones] = useState(false);
  const [biasMode, setBiasMode] = useState("off");
  const [requireDisplacement, setRequireDisplacement] = useState(true);
  const [invalidationOn, setInvalidationOn] = useState(true);
  const [invalidationWindow, setInvalidationWindow] = useState(2);
  const [invalidationConfirmBars, setInvalidationConfirmBars] = useState(1);
  const [invalidationGraceBars, setInvalidationGraceBars] = useState(10);
  const [pyramidOn, setPyramidOn] = useState(false);
  const [pyramidTriggerR, setPyramidTriggerR] = useState(1.1);
  const [pyramidRiskMult, setPyramidRiskMult] = useState(0.5);
  const [riskPct, setRiskPct] = useState(1.0);
  const [startEquity, setStartEquity] = useState(2000);

  const [auditMode, setAuditMode] = useState(true);
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
      const data = await runDailyObBacktest({
        symbols, htf_bars: htfBars, htf_timeframe: htfTimeframe, swing_window: swingWindow,
        min_rr: minRr, max_rr: maxRr, use_killzones: useKillzones,
        bias_mode: biasMode, require_displacement: requireDisplacement,
        use_invalidation_exit: invalidationOn, invalidation_minor_window: invalidationWindow,
        invalidation_confirm_bars: invalidationConfirmBars, invalidation_grace_bars: invalidationGraceBars,
        pyramid_trigger_r: pyramidOn ? pyramidTriggerR : null,
        pyramid_risk_mult: pyramidRiskMult,
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

        <h3>Order block timeframe</h3>
        <label className="checkbox-row">
          <input type="radio" checked={htfTimeframe === "D1"}
                 onChange={() => { setHtfTimeframe("D1"); setHtfBars(2600); }} />
          Daily (D1)
        </label>
        <label className="checkbox-row">
          <input type="radio" checked={htfTimeframe === "H4"}
                 onChange={() => { setHtfTimeframe("H4"); setHtfBars(9000); }} />
          4-Hour (H4)
        </label>
        <p className="hint">D1 uses H1 entries; H4 uses M15 entries. Compare corrected runs over the same data period.</p>

        <label>History depth ({htfTimeframe} bars): {htfBars.toLocaleString()}</label>
        <input type="range" min={500} max={htfTimeframe === "H4" ? 9000 : 2600} step={100} value={htfBars}
               onChange={(e) => setHtfBars(Number(e.target.value))} />

        <label>Swing window (fractal size): {swingWindow}</label>
        <input type="range" min={1} max={6} value={swingWindow}
               onChange={(e) => setSwingWindow(Number(e.target.value))} />

        <label>Min RR (structural target): {minRr.toFixed(1)}</label>
        <input type="range" min={1} max={5} step={0.5} value={minRr}
               onChange={(e) => setMinRr(Number(e.target.value))} />

        <label>Max RR cap: {maxRr.toFixed(1)}</label>
        <input type="range" min={1} max={8} step={0.5} value={maxRr}
               onChange={(e) => setMaxRr(Number(e.target.value))} />

        <label className="checkbox-row">
          <input type="checkbox" checked={useKillzones} onChange={(e) => setUseKillzones(e.target.checked)} />
          Restrict entries to London/NY killzones
        </label>
        <p className="hint">Off by default — this is a daily-swing strategy, not a session-timing one.</p>

        <label className="checkbox-row">
          <input type="checkbox" checked={requireDisplacement}
                 onChange={(e) => setRequireDisplacement(e.target.checked)} />
          Require real displacement (FVG behind the OB)
        </label>
        <p className="hint">Monitoring starts only after both the OB breakout and its qualifying FVG have closed.</p>

        <h3>Early invalidation exit</h3>
        <label className="checkbox-row">
          <input type="checkbox" checked={invalidationOn} onChange={(e) => setInvalidationOn(e.target.checked)} />
          Cut losers early on a structure break against the trade
        </label>
        <p className="hint">A confirmed structure break schedules a next-open exit for the OB chain. Grace bars delay monitoring.</p>
        <label>Grace period (bars before it can trigger): {invalidationGraceBars}</label>
        <input type="range" min={0} max={30} value={invalidationGraceBars} disabled={!invalidationOn}
               onChange={(e) => setInvalidationGraceBars(Number(e.target.value))} />
        <label>Swing window (bigger = less noise-sensitive): {invalidationWindow}</label>
        <input type="range" min={1} max={5} value={invalidationWindow} disabled={!invalidationOn}
               onChange={(e) => setInvalidationWindow(Number(e.target.value))} />
        <label>Confirm bars (consecutive closes required): {invalidationConfirmBars}</label>
        <input type="range" min={1} max={3} value={invalidationConfirmBars} disabled={!invalidationOn}
               onChange={(e) => setInvalidationConfirmBars(Number(e.target.value))} />

        <h3>Higher-timeframe trend bias</h3>
        <div className="mode-filter">
          {[
            { key: "off", label: "Off — trade the OB direction" },
            { key: "filter", label: "Filter — skip counter-trend OBs" },
            { key: "reverse", label: "Reverse — reverse counter-trend OBs" },
          ].map((opt) => (
            <label key={opt.key} className="radio-row">
              <input
                type="radio"
                name="biasMode"
                checked={biasMode === opt.key}
                onChange={() => setBiasMode(opt.key)}
              />
              {opt.label}
            </label>
          ))}
        </div>
        <p className="hint">Bias uses completed higher-timeframe candles only. Compare modes using the corrected engine.</p>

        <h3>Pyramiding</h3>
        <label className="checkbox-row">
          <input type="checkbox" checked={pyramidOn} onChange={(e) => setPyramidOn(e.target.checked)} />
          Add a second leg once the base trade is up
        </label>
        <p className="hint">The add risks the selected fraction of normal risk. It opens only while the base is still active; structure invalidation applies to both legs.</p>
        <label>Trigger (R in favor): {pyramidTriggerR.toFixed(1)}</label>
        <input type="range" min={0.3} max={3} step={0.1} value={pyramidTriggerR} disabled={!pyramidOn}
               onChange={(e) => setPyramidTriggerR(Number(e.target.value))} />
        <label>Add size (x normal risk): {pyramidRiskMult.toFixed(1)}</label>
        <input type="range" min={0.1} max={1} step={0.1} value={pyramidRiskMult} disabled={!pyramidOn}
               onChange={(e) => setPyramidRiskMult(Number(e.target.value))} />

        <label>Risk % per trade: {riskPct.toFixed(2)}</label>
        <input type="range" min={0.25} max={5} step={0.25} value={riskPct}
               onChange={(e) => setRiskPct(Number(e.target.value))} />

        <CapitalInput value={startEquity} onChange={setStartEquity} />

        <button className="run-btn" onClick={handleRun} disabled={loading || symbols.length === 0 || minRr > maxRr || startEquity <= 0}>
          {loading ? "Running…" : "Run backtest"}
        </button>
        {errorMsg && <div className="error">{errorMsg}</div>}
      </aside>

      <main className="main">
        <h1>Daily Order Block retracement</h1>
        <p className="subtitle">
          Wait for a confirmed OB and any required displacement, then enter on the next open
          after a retracement signal. Results use realized cash and gross prices, excluding
          spread, commissions, slippage and financing. Open-position drawdown is not included.
        </p>

        {result && Object.keys(result.errors || {}).length > 0 && (
          <div className="error">Partial/failed run: {Object.entries(result.errors).map(([sym, error]) => `${sym}: ${error}`).join("; ")}</div>
        )}
        {result && <p className="hint">Run {result.run_id} · {result.engine_version}. Results use saved run settings; edited controls apply after Run backtest.</p>}
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
              <Metric label="Realized-balance drawdown" value={`${result.metrics.max_drawdown_pct.toFixed(1)}%`} />
              <Metric label="CAGR (annualized)" value={result.metrics.cagr_pct != null ? `${result.metrics.cagr_pct.toFixed(1)}%` : "—"} />
              <Metric label="Final equity" value={fmtMoney(result.metrics.final_equity)} />
              <Metric label="Initial risk/trade" value={fmtMoney(result.config.start_equity * (result.config.risk_pct / 100))} />
            </div>
            {result.metrics.years_span != null && (
              <p className="hint">
                Over {result.metrics.years_span.toFixed(1)} years of data coverage.
                CAGR annualizes the corrected realized balance; it does not account for
                trading costs or capacity limits. Per-symbol history can differ.
              </p>
            )}

            <EquityCurve equity={result.metrics.equity_curve} times={result.metrics.equity_times} />

            <MonteCarloPanel key={result.run_id} trades={result.trades} riskPct={result.config.risk_pct} startEquity={result.config.start_equity} />

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
            <label className="checkbox-row"><input type="checkbox" checked={auditMode} onChange={e => setAuditMode(e.target.checked)} />Hide individual outcomes until revealed in replay</label>
            <div className="trade-table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th></th><th>Symbol</th><th>OB type</th><th>Dir</th><th>Leg</th>
                    <th>Entry time</th><th>Outcome</th><th>R</th><th>Risked</th><th>{"P&L"}</th><th>Balance</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((t, i) => {
                    const { riskAmount, pnlAmount, balanceAfter } = tradeMoney(result.trades, result.metrics.equity_curve, result.config.risk_pct, i);
                    return (
                    <tr
                      key={t.id}
                      className={t.id === selectedTradeId ? "row-selected" : ""}
                      onClick={() => setSelectedTradeId(t.id)}
                    >
                      <td><input type="radio" readOnly checked={t.id === selectedTradeId} /></td>
                      <td>{t.symbol}</td>
                      <td>{t.ob_type}</td>
                      <td>{t.direction}</td>
                      <td>{t.leg_type || "base"}</td>
                      <td>{t.entry_time?.replace("T", " ").slice(0, 16)}</td>
                      <td>{auditMode ? "Hidden" : t.outcome}</td>
                      <td className={auditMode ? "" : t.r_multiple > 0 ? "pos" : "neg"}>{auditMode ? "—" : t.r_multiple?.toFixed(2)}</td>
                      <td>{fmtMoney(riskAmount)}</td>
                      <td className={auditMode ? "" : pnlAmount > 0 ? "pos" : "neg"}>{auditMode ? "—" : fmtMoney(pnlAmount)}</td>
                      <td>{auditMode ? "—" : fmtMoney(balanceAfter)}</td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <h3>Candle replay</h3>
            {selectedTrade ? (
              <TradeReplay trade={selectedTrade} ltf={result.ltf} auditMode={auditMode} />
            ) : (
              <div className="info-box">Click a row above to replay that trade.</div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
