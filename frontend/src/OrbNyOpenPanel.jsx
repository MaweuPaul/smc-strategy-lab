import { useEffect, useMemo, useState } from "react";
import { fetchSymbols, runOrbNyOpenBacktest } from "./api";
import TradeReplay from "./TradeReplay";
import { Metric, EquityCurve, SymbolPicker, CapitalInput, fmtMoney, tradeMoney } from "./Shared";

const DOW_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Groups trades by a bucket key (day-of-week or month), pulling each
// trade's real dollar P&L from the equity-curve delta at its own index
// (trades are sorted by entry_time and 1:1 with the curve, same source
// tradeMoney() uses for the trade-log table) rather than approximating it
// from avg R and the current risk %, which would be wrong for a
// multi-symbol run where risk was taken at different equity levels.
function computeBucketStats(trades, equityCurve, names, keyFn) {
  const buckets = names.map((name) => ({ name, n: 0, wins: 0, totalR: 0, totalPnl: 0 }));
  trades.forEach((t, i) => {
    const key = keyFn(t);
    const b = buckets[key];
    if (!b) return;
    b.n += 1;
    if (t.r_multiple > 0) b.wins += 1;
    b.totalR += t.r_multiple ?? 0;
    if (equityCurve && i < equityCurve.length - 1) {
      b.totalPnl += equityCurve[i + 1] - equityCurve[i];
    }
  });
  return buckets.map((b) => ({
    ...b,
    winPct: b.n ? (100 * b.wins) / b.n : 0,
    avgR: b.n ? b.totalR / b.n : 0,
  }));
}

function BucketGrid({ buckets, cols }) {
  const active = buckets.filter((b) => b.n > 0);
  const best = active.length ? active.reduce((a, b) => (b.avgR > a.avgR ? b : a)) : null;
  const worst = active.length > 1 ? active.reduce((a, b) => (b.avgR < a.avgR ? b : a)) : null;
  return (
    <div className="seasonality-grid" style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
      {buckets.map((b) => (
        <div
          key={b.name}
          className={
            "season-cell" +
            (b.n === 0 ? " season-empty" : b.avgR > 0 ? " season-pos" : " season-neg") +
            (best && b.name === best.name && b.n > 0 ? " season-best" : "") +
            (worst && b.name === worst.name && b.n > 0 ? " season-worst" : "")
          }
        >
          <div className="season-month">{b.name}</div>
          {b.n > 0 ? (
            <>
              <div className="season-r">{b.avgR >= 0 ? "+" : ""}{b.avgR.toFixed(2)}R</div>
              <div className="season-sub">{b.n} trades · {b.winPct.toFixed(0)}% win</div>
              <div className="season-sub">{fmtMoney(b.totalPnl)}</div>
            </>
          ) : (
            <div className="season-sub">no trades</div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function OrbNyOpenPanel() {
  const [symbolsList, setSymbolsList] = useState([]);
  const [symbols, setSymbols] = useState(["XAUUSD", "USTEC", "US30"]);
  const [bars, setBars] = useState(700_000);
  const [candleMinutes, setCandleMinutes] = useState(5);
  const [monitorHours, setMonitorHours] = useState(6);
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
      const data = await runOrbNyOpenBacktest({
        symbols, bars, candle_minutes: candleMinutes, monitor_hours: monitorHours,
        target_mode: targetMode, target_range_mult: targetRangeMult,
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

  const dowStats = useMemo(() => {
    if (!result?.trades?.length) return null;
    const buckets = computeBucketStats(result.trades, result.metrics.equity_curve, DOW_NAMES,
      (t) => new Date(t.entry_time).getUTCDay());
    // NY-open trades only ever fire Mon-Fri; drop the always-empty weekend cells
    return buckets.filter((b) => b.name !== "Sat" && b.name !== "Sun");
  }, [result]);

  const monthStats = useMemo(() => {
    if (!result?.trades?.length) return null;
    return computeBucketStats(result.trades, result.metrics.equity_curve, MONTH_NAMES,
      (t) => new Date(t.entry_time).getUTCMonth());
  }, [result]);

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
        <label>Marking candle, ending at 9:30am New York</label>
        <div className="mode-filter">
          {[
            { key: 1, label: "1-minute (9:29-9:30 NY)" },
            { key: 5, label: "5-minute (9:25-9:30 NY)" },
          ].map((opt) => (
            <label key={opt.key} className="radio-row">
              <input type="radio" name="candleMinutes" checked={candleMinutes === opt.key}
                     onChange={() => setCandleMinutes(opt.key)} />
              {opt.label}
            </label>
          ))}
        </div>
        <p className="hint">
          9:30am New York is DST-aware (13:30 UTC in winter, 14:30 UTC in summer) -- computed from
          real timezone data, not a fixed UTC hour, so it stays correct across the clock changes.
          5-minute tested better than 1-minute on every symbol (fewer false breakouts from a wider
          marking range) -- that's the default here.
        </p>

        <label>Monitor window (hours after open): {monitorHours}</label>
        <input type="range" min={1} max={12} step={0.5} value={monitorHours}
               onChange={(e) => setMonitorHours(Number(e.target.value))} />

        <h3>Target</h3>
        <div className="mode-filter">
          {[
            { key: "measured_move", label: "Measured move (range width x)" },
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
            <label>Target = range width x: {targetRangeMult.toFixed(1)}</label>
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
          Daily bias filter (today's range midpoint vs. yesterday's)
        </label>
        <p className="hint">
          Roughly halves trade count but improved every tested symbol -- e.g. Gold PF 1.00 → 1.33,
          USTEC PF 0.76 → 1.18 (at 5-minute candle, measured-move x1). On by default.
        </p>

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
        <h1>ORB — New York Open</h1>
        <p className="subtitle">
          Marks the high/low of the last M1 candle(s) immediately BEFORE the 9:30am New York open,
          then trades the first M1 close beyond either side: break above → buy, break below → sell.
          Stop at the opposite side of that range; target is either a multiple of the range's own
          width (measured move) or RR-based. One trade per day. Data pulled live from your MT5
          terminal (read-only).
        </p>
        <p className="subtitle" style={{ color: "#e0a000" }}>
          ⚠ Not modeled: slippage around major news releases. Stops fill at the exact bar-close
          level this simulation assumes -- a stop just beyond a tight NY-open range is exactly the
          kind of level that can slip further during high-impact releases (NFP, CPI, FOMC), and NY
          open itself is a common release window. Treat these numbers as an upper bound on live
          performance around news, not a guarantee.
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
              <Metric label="Avg R" value={result.metrics.avg_r.toFixed(3)} />
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
                    <td>{m.win_rate.toFixed(1)}%</td><td>{m.avg_r.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {dowStats && (
              <>
                <h3>By day of week</h3>
                <p className="hint">
                  Every trade grouped by its entry weekday, across all symbols and the whole history
                  pulled. If one or two days are consistently red while the rest carry the edge, that's
                  worth excluding rather than diluting the average into "roughly breakeven overall."
                </p>
                <BucketGrid buckets={dowStats} cols={5} />
              </>
            )}

            {monthStats && (
              <>
                <h3>By month</h3>
                <p className="hint">
                  Same grouping, by calendar month across all years pulled. Small per-month counts mean
                  noisy R-multiples -- a single red or green month is a lead to dig into, not a rule to
                  trade on by itself.
                </p>
                <BucketGrid buckets={monthStats} cols={6} />
              </>
            )}

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
