import { useState, useMemo } from "react";
import { runDailyFvgBacktest } from "./api";
import DailyFvgReplay from "./DailyFvgReplay";
import MonteCarloPanel from "./MonteCarlo";
import { Metric, EquityCurve, fmtMoney } from "./Shared";

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Month-of-year seasonality: group every trade by its entry month (UTC),
// regardless of which year it happened in, so "October tends to win" can
// be checked against the whole history at once rather than eyeballing
// the trade log. Computed straight from the returned trades -- no backend
// change needed, since entry_time is already a unix timestamp per trade.
function computeSeasonality(trades) {
  const byMonth = Array.from({ length: 12 }, (_, i) => ({
    month: MONTH_NAMES[i], count: 0, wins: 0, totalR: 0, totalPnl: 0,
  }));
  for (const t of trades) {
    const m = new Date(t.entry_time * 1000).getUTCMonth();
    const row = byMonth[m];
    row.count += 1;
    if (t.pnl > 0) row.wins += 1;
    row.totalR += t.r_multiple;
    row.totalPnl += t.pnl;
  }
  return byMonth.map((row) => ({
    ...row,
    winPct: row.count ? (100 * row.wins) / row.count : 0,
    avgR: row.count ? row.totalR / row.count : 0,
  }));
}

const SYMBOLS = ["USTEC", "US500", "US30", "EURUSD", "XAUUSD", "XAGUSD", "GBPUSD", "USDJPY"];
const PRICE_DECIMALS = { USTEC: 2, US500: 2, US30: 2, XAUUSD: 2, XAGUSD: 2, USDJPY: 3 };
const METALS = ["XAUUSD", "XAGUSD"];
// RR is the real win-rate lever: a closer target wins more often but wins
// less each time. Gold used to default to 3:1 because that maximised avg R
// -- but avg R ignores that a closer target also fires MORE trades, and on
// every measure that actually matters 2:1 beats 3:1 on Gold: 55.3% win vs
// 44.4%, PF 2.37 vs 2.23, $241k vs $222k final, identical -10.5% drawdown
// (and it holds up on the clean post-2021 M15 window too: 57.7% win, PF
// 2.57). Silver genuinely does peak at 3:1, so it keeps it. FX/indices get
// worse the longer you hold. See daily_fvg_winrate_levers.py.
const defaultRrFor = (sym) => (sym === "XAGUSD" ? 3 : 2);
// Resistance filter (skip a buy if a prior unbroken D1 swing high sits
// closer than the target) only tested as a net improvement on GBPUSD --
// it meaningfully hurts Gold/Silver/USTEC and is a wash on EURUSD/USDJPY.
// See daily_fvg_resistance_filter.py's per-symbol sweep for the numbers.
const defaultResistanceFilterFor = (sym) => sym === "GBPUSD";
// ATR stop-buffer sweep (daily_fvg_newday.py): wider is better almost
// everywhere -- 1.0x is a well-sampled sweet spot for metals, USDJPY
// actually peaks at 0.75x and gets worse beyond it, EURUSD/GBPUSD stay
// noisy/negative regardless so keep the original 0.25x for them.
const defaultAtrBufferFor = (sym) => {
  if (METALS.includes(sym)) return 1.0;
  if (sym === "USDJPY") return 0.75;
  return 0.25;
};
// Continuation (buy right after the gap confirms, no retracement wait)
// beat retracement on USTEC/EURUSD/Silver/USDJPY but lost clearly on
// Gold (the flagship result) and GBPUSD. See daily_fvg_continuation.py's
// side-by-side sweep for the numbers.
// Both the magnet leg (daily_fvg_ride_to_magnet.py) and the H4 bias filter
// (daily_fvg_ltf_bias.py) were only ever tested against the plain
// RETRACEMENT buy -- never against continuation entries. So whenever
// either defaults ON for a symbol, entry style must default to
// retracement too, or the shipped default would be a combination nobody
// has actually measured.
const defaultEntryStyleFor = (sym) =>
  (["XAUUSD", "GBPUSD"].includes(sym) || defaultMagnetLegFor(sym) || defaultLtfBiasFor(sym))
    ? "retracement" : "continuation";
// US500/US30 tested the same as USTEC (same RR=2/buf=0.25 defaults, same
// find_leg1_short_entries + retracement combo) and perform just as well:
// avgR +0.30/+0.43R combined, PF 1.4/1.64, in line with USTEC's +0.45R/1.59.
// See the ad-hoc sweep run alongside daily_fvg_ride_to_magnet.py.
// "Ride to the magnet" short leg -- TESTED AND FAILED, off everywhere.
// Its original strong numbers (+0.64R USTEC, +0.65R Gold) turned out to be
// pure look-ahead bias: the entry was triggered off a bar's LOW touching
// the gap, then filled at that same bar's OPEN, which sits above the
// trigger ~99% of the time (mean ~$10.9 Gold, ~56pts USTEC) -- free money
// no live fill could ever get. Once it fills at its own trigger level like
// a real sell-limit, every symbol's short leg is deeply negative (-0.24R
// Gold, -0.62R USTEC). Kept as an opt-in toggle so the result is
// inspectable, but it should stay off. See daily_fvg_ride_to_magnet.py.
const defaultMagnetLegFor = () => false;
// H4/H1 BOS+CHoCH bias filter: only buy when lower-timeframe structure
// reads bullish (LuxAlgo definition -- bias flips on a close through the
// last unbroken pivot, so it turns far sooner than the daily HH/HL read
// that failed in daily_fvg_structure_filter.py). Improved win rate, avg
// R, PF, drawdown AND worst losing streak on 5 of 6 symbols; drawdown
// gains beat 87-99.8% of random same-size trade removals, so it is real
// selection rather than just trading less. US500 is the one symbol where
// it does worse than random, so it defaults off there. Pivot size defaults
// to 5 = LuxAlgo's "internal structure" default, so the panel agrees with
// what the indicator draws on the user's own chart; size 5 and 10 backtest
// about equally (each better on 3 of 6 symbols), so that tie is broken on
// tool-agreement rather than on a curve-fit. See daily_fvg_ltf_bias.py.
const defaultLtfBiasFor = (sym) => sym !== "US500";

export default function DailyFvgPanel() {
  const [symbol, setSymbol] = useState("XAUUSD");
  const [monthsBack, setMonthsBack] = useState(96);
  const [targetRR, setTargetRR] = useState(defaultRrFor("XAUUSD"));
  const [atrBufferMult, setAtrBufferMult] = useState(defaultAtrBufferFor("XAUUSD"));
  const [useResistanceFilter, setUseResistanceFilter] = useState(defaultResistanceFilterFor("XAUUSD"));
  const [entryStyle, setEntryStyle] = useState(defaultEntryStyleFor("XAUUSD"));
  const [useMagnetLeg, setUseMagnetLeg] = useState(defaultMagnetLegFor("XAUUSD"));
  const [useLtfBias, setUseLtfBias] = useState(defaultLtfBiasFor("XAUUSD"));
  const [ltfBiasTf, setLtfBiasTf] = useState("H4");
  const [ltfBiasSize, setLtfBiasSize] = useState(5);
  const [magnetLegRiskMult, setMagnetLegRiskMult] = useState(0.5);
  const [startEquity, setStartEquity] = useState(100000);
  const [riskPct, setRiskPct] = useState(1.5);
  const [sizingMode, setSizingMode] = useState("risk_pct");
  const [fixedLots, setFixedLots] = useState(0.1);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [selectedIdx, setSelectedIdx] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);
  const [chartTf, setChartTf] = useState("D1");
  const [yearFilter, setYearFilter] = useState("all");

  function selectSymbol(sym) {
    setSymbol(sym);
    setTargetRR(defaultRrFor(sym));
    setAtrBufferMult(defaultAtrBufferFor(sym));
    setUseResistanceFilter(defaultResistanceFilterFor(sym));
    setEntryStyle(defaultEntryStyleFor(sym));
    setUseMagnetLeg(defaultMagnetLegFor(sym));
    setUseLtfBias(defaultLtfBiasFor(sym));
  }

  async function handleRun() {
    setLoading(true);
    setErrorMsg(null);
    setSelectedIdx(null);
    try {
      const data = await runDailyFvgBacktest({
        symbol, months_back: monthsBack, target_rr: targetRR,
        atr_buffer_mult: atrBufferMult, use_resistance_filter: useResistanceFilter,
        entry_style: entryStyle, use_magnet_leg: useMagnetLeg, magnet_leg_risk_mult: magnetLegRiskMult,
        start_equity: startEquity, risk_pct: riskPct,
        sizing_mode: sizingMode, fixed_lots: fixedLots,
        use_ltf_bias: useLtfBias, ltf_bias_timeframe: ltfBiasTf, ltf_bias_size: ltfBiasSize,
      });
      setResult(data);
      setYearFilter("all");
      if (data.trades.length > 0) setSelectedIdx(0);
    } catch (e) {
      setErrorMsg(e?.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  }

  const decimals = PRICE_DECIMALS[symbol] ?? 5;
  const selectedTrade = result && selectedIdx != null ? result.trades[selectedIdx] : null;

  const availableYears = useMemo(() => {
    if (!result?.trades?.length) return [];
    const years = new Set(result.trades.map((t) => new Date(t.entry_time * 1000).getUTCFullYear()));
    return Array.from(years).sort((a, b) => a - b);
  }, [result]);

  // (originalIndex, trade) pairs so table row selection / chart trade
  // lookup still points at the right entry in result.trades after
  // filtering by year -- filteredTrades itself is a subset view, not
  // a reindexed copy.
  const filteredTrades = useMemo(() => {
    if (!result?.trades?.length) return [];
    return result.trades
      .map((t, originalIndex) => ({ t, originalIndex }))
      .filter(({ t }) => yearFilter === "all" || new Date(t.entry_time * 1000).getUTCFullYear() === yearFilter);
  }, [result, yearFilter]);

  const periodSummary = useMemo(() => {
    if (yearFilter === "all" || !filteredTrades.length) return null;
    const trades = filteredTrades.map(({ t }) => t);
    const wins = trades.filter((t) => t.pnl > 0).length;
    const profit = trades.filter((t) => t.pnl > 0).reduce((s, t) => s + t.pnl, 0);
    const loss = -trades.filter((t) => t.pnl < 0).reduce((s, t) => s + t.pnl, 0);
    const totalPnl = trades.reduce((s, t) => s + t.pnl, 0);
    const avgR = trades.reduce((s, t) => s + t.r_multiple, 0) / trades.length;
    return {
      n: trades.length, winPct: (100 * wins) / trades.length, avgR,
      pf: loss ? profit / loss : null, totalPnl,
    };
  }, [filteredTrades, yearFilter]);

  const seasonality = useMemo(() => {
    const trades = filteredTrades.map(({ t }) => t);
    return trades.length ? computeSeasonality(trades) : null;
  }, [filteredTrades]);
  const activeMonths = seasonality ? seasonality.filter((m) => m.count > 0) : [];
  const bestMonth = activeMonths.length ? activeMonths.reduce((a, b) => (b.avgR > a.avgR ? b : a)) : null;
  const worstMonth = activeMonths.length ? activeMonths.reduce((a, b) => (b.avgR < a.avgR ? b : a)) : null;

  return (
    <div className="app">
      <aside className="sidebar">
        <h2>Settings</h2>

        <label>Symbol</label>
        <div className="symbol-grid">
          {SYMBOLS.map((s) => (
            <button key={s} className={"chip" + (symbol === s ? " chip-active" : "")} onClick={() => selectSymbol(s)}>
              {s}
            </button>
          ))}
        </div>

        <label>History depth (months back): {monthsBack}</label>
        <input type="range" min={1} max={300} value={monthsBack} onChange={(e) => setMonthsBack(Number(e.target.value))} />

        <label>Starting capital ($)</label>
        <input
          type="number" min={1} step={1000} value={startEquity}
          onChange={(e) => setStartEquity(Math.max(1, Number(e.target.value) || 0))}
        />

        <h3>Entry style</h3>
        <div className="mode-filter">
          {[
            { key: "retracement", label: "Retracement -- wait for price to pull back into the gap" },
            { key: "continuation", label: "Continuation -- buy right after the gap confirms, no wait" },
          ].map((opt) => (
            <label key={opt.key} className="radio-row">
              <input type="radio" name="entryStyle" checked={entryStyle === opt.key} onChange={() => setEntryStyle(opt.key)} />
              {opt.label}
            </label>
          ))}
        </div>
        <p className="hint">
          Default {defaultEntryStyleFor(symbol)} for {symbol}. Continuation beat retracement on USTEC/EURUSD/Silver/
          USDJPY, but retracement is clearly better on Gold and GBPUSD. The resistance filter only applies to
          retracement entries.
        </p>

        <label>Target reward:risk: {targetRR.toFixed(1)}:1</label>
        <input type="range" min={1} max={6} step={0.5} value={targetRR} onChange={(e) => setTargetRR(Number(e.target.value))} />
        <p className="hint">
          Default {defaultRrFor(symbol)}:1 for {symbol}. This is the main win-rate lever -- a closer
          target wins more often but wins less each time, so judge it on profit factor and final equity,
          not win rate alone. Gold peaks at 2:1 (55% win, PF 2.37); Silver is the one symbol that really
          does peak at 3:1. Dropping to 1:1 buys a ~55-63% win rate everywhere but loses money on every
          symbol except Gold.
        </p>

        <h3>Position sizing</h3>
        <div className="mode-filter">
          {[
            { key: "risk_pct", label: "% risk -- size scales with stop distance" },
            { key: "fixed_units", label: "Fixed size -- same size every trade" },
          ].map((opt) => (
            <label key={opt.key} className="radio-row">
              <input type="radio" name="sizingMode" checked={sizingMode === opt.key} onChange={() => setSizingMode(opt.key)} />
              {opt.label}
            </label>
          ))}
        </div>

        {sizingMode === "risk_pct" ? (
          <>
            <label>Risk per trade: {riskPct.toFixed(2)}%</label>
            <input type="range" min={0.1} max={5} step={0.1} value={riskPct} onChange={(e) => setRiskPct(Number(e.target.value))} />
            <p className="hint">
              Fraction of current equity risked on each trade (position size = risk amount / stop distance,
              capped at 5x leverage). A fast-moving instrument like Gold has a wide, swingy ATR stop, so this
              mode's position size swings trade to trade too -- switch to Fixed size if you'd rather trade the
              same size every time and let R vary instead.
            </p>
          </>
        ) : (
          <>
            <label>Position size (lots per trade)</label>
            <input
              type="number" min={0.01} step={0.01} value={fixedLots}
              onChange={(e) => setFixedLots(Math.max(0.01, Number(e.target.value) || 0))}
            />
            <p className="hint">
              Same lot size every trade regardless of stop distance. Simpler to place manually, but it
              measurably costs money here: losing trades carry ~1.3x the stop distance of winners
              (Gold: 76 vs 58 $/oz), so at a fixed lot the dollar losses are systematically bigger than
              the dollar wins and a nominal 2:1 pays out nearer 1.4:1. On identical Gold trades that is
              PF 1.74 fixed-lot vs 2.37 on % risk. % risk shrinks the position exactly when the stop is
              wide, which is what keeps the payoff ratio intact. This is a real MT5 lot (converted using
              {" "}{symbol}'s contract size from your terminal), so 0.10 here means 0.10 on your ticket.
            </p>
          </>
        )}

        <label>Stop-loss buffer: {atrBufferMult.toFixed(2)}x ATR14</label>
        <input type="range" min={0.1} max={2.0} step={0.05} value={atrBufferMult} onChange={(e) => setAtrBufferMult(Number(e.target.value))} />
        <p className="hint">
          Default {defaultAtrBufferFor(symbol).toFixed(2)}x for {symbol}. Counterintuitively, WIDER is better almost
          everywhere -- tight stops mostly just get clipped by noise before the setup gets a chance. USDJPY is the
          exception: it peaks around 0.75x and gets worse beyond that. EURUSD/GBPUSD stay unreliable regardless.
        </p>

        <label className="checkbox-row">
          <input type="checkbox" checked={useResistanceFilter} disabled={entryStyle === "continuation"}
                 onChange={(e) => setUseResistanceFilter(e.target.checked)} />
          Skip buys blocked by prior resistance {entryStyle === "continuation" && "(retracement only)"}
        </label>
        <p className="hint">
          Skips a signal if a prior unbroken D1 swing high sits closer to entry than the target.
          Only tested as a net improvement on GBPUSD ({defaultResistanceFilterFor(symbol) ? "on" : "off"} by default
          for {symbol}) -- it meaningfully hurts Gold/Silver/USTEC.
        </p>

        <label className="checkbox-row">
          <input type="checkbox" checked={useLtfBias} onChange={(e) => setUseLtfBias(e.target.checked)} />
          Require bullish {ltfBiasTf} structure (BOS/CHoCH bias)
        </label>
        <p className="hint">
          Only buys when lower-timeframe structure reads bullish -- bias flips on a close through the last
          unbroken pivot, so it turns much sooner than a daily HH/HL read (which flipped too late to help).
          Best result found this session: improved win rate, avg R, profit factor, drawdown AND worst losing
          streak on 5 of 6 symbols, e.g. US30's worst losing run 12 → 6. The drawdown gains beat 87-99.8% of
          random same-size trade removals, so it's genuine selection, not just trading less.
          {defaultLtfBiasFor(symbol) ? " On" : " Off"} by default for {symbol}
          {symbol === "US500" && " -- it's the one symbol where it does worse than random"}.
          It roughly halves trade count, so consider raising risk per trade to use the freed-up drawdown.
        </p>

        {useLtfBias && (
          <>
            <label>Structure timeframe</label>
            <div className="symtabs-row">
              {["H4", "H1"].map((tf) => (
                <button key={tf} className={"chip" + (ltfBiasTf === tf ? " chip-active" : "")}
                        onClick={() => setLtfBiasTf(tf)}>
                  {tf}
                </button>
              ))}
            </div>
            <label>Pivot lookback: {ltfBiasSize} bars</label>
            <input type="range" min={2} max={50} step={1} value={ltfBiasSize}
                   onChange={(e) => setLtfBiasSize(Number(e.target.value))} />
            <p className="hint">
              How many bars must pass without exceeding a high/low before it counts as a pivot. This maps
              directly onto the LuxAlgo SMC indicator: <strong>5 = its "internal structure"</strong> (the
              default here, so the panel agrees with what your chart shows), <strong>50 = its "swing
              structure"</strong>. Size 5 and 10 backtest about equally well overall — each is better on
              three symbols — so 5 is chosen to match the indicator rather than on backtest grounds.
              <strong> Note:</strong> your MT5 terminal only holds H4/H1 history back to ~2021-07, so turning
              this on effectively limits the backtest to that window.
            </p>
          </>
        )}

        <label className="checkbox-row">
          <input type="checkbox" checked={useMagnetLeg} onChange={(e) => setUseMagnetLeg(e.target.checked)} />
          Ride to the magnet (short into unfilled gaps before buying the retracement)
        </label>
        <p className="hint">
          <strong>Tested and failed -- leave off.</strong> Adds a short leg that fires once price trades
          back into an unfilled bullish gap, targeting the full fill. It originally looked strong
          (+0.65R on Gold), but that was look-ahead bias: it filled at the bar's open after using that
          same bar's low to trigger, ~$10.9 above its own entry level on Gold. Filling honestly at the
          trigger level, every symbol goes negative (-0.24R Gold, -0.62R USTEC). Kept only so the result
          stays inspectable.
        </p>

        {useMagnetLeg && (
          <>
            <label>Magnet short size: {magnetLegRiskMult.toFixed(2)}x normal size</label>
            <input type="range" min={0.1} max={1.0} step={0.05} value={magnetLegRiskMult}
                   onChange={(e) => setMagnetLegRiskMult(Number(e.target.value))} />
            <p className="hint">
              Sizes the magnet short relative to the retracement buy. The drawdown-reduction numbers this
              was originally tuned on came from the look-ahead-biased version of the short leg, so treat
              this slider as untuned -- the honest short leg loses money at any size.
            </p>
          </>
        )}

        <button className="run-btn" onClick={handleRun} disabled={loading}>
          {loading ? "Running…" : "Run backtest"}
        </button>
        {errorMsg && <div className="error">{errorMsg}</div>}
      </aside>

      <main className="main main-wide">
        <h1>Daily FVG Retracement</h1>
        <p className="subtitle">
          Wait for price to retrace into a bullish daily Fair Value Gap, buy at the next day's open
          (executed on M15). Stop = gap bottom minus 0.25x D1 ATR14. Target = fixed reward:risk
          (configurable, left), single exit. Gross P&amp;L, excludes spread/slippage beyond the
          modeled 0.05% commission per fill.{" "}
          {sizingMode === "risk_pct"
            ? `${riskPct.toFixed(2)}% risk/trade, 5x max leverage`
            : `Fixed ${fixedLots.toFixed(2)} lot/trade`}{" "}
          (configurable, left), {fmtMoney(startEquity)} starting equity (configurable, left).
        </p>

        {!result && <div className="info-box">Pick a symbol and history depth, then click Run backtest.</div>}

        {result && result.trades.length === 0 && (
          <div className="info-box">No trades generated over this window.</div>
        )}

        {result && result.metrics && (
          <>
            <div className="metrics-row">
              <Metric label="Target RR" value={`${result.metrics.target_rr.toFixed(1)}:1`} />
              <Metric
                label={result.metrics.sizing_mode === "fixed_units" ? "Position size" : "Risk/trade"}
                value={result.metrics.sizing_mode === "fixed_units" ? `${result.metrics.fixed_lots.toFixed(2)} lot` : `${result.metrics.risk_pct.toFixed(2)}%`}
              />
              <Metric label="SL buffer" value={`${result.metrics.atr_buffer_mult.toFixed(2)}x ATR`} />
              <Metric label="Entry style" value={result.metrics.entry_style} />
              {result.metrics.use_magnet_leg && (
                <Metric label="Magnet leg" value={`on (${result.metrics.magnet_leg_risk_mult.toFixed(2)}x)`} />
              )}
              <Metric label="Trades" value={result.metrics.trades} />
              <Metric label="Win rate" value={`${result.metrics.win_rate.toFixed(1)}%`} />
              <Metric label="Avg R" value={result.metrics.avg_r.toFixed(3)} />
              <Metric label="Profit factor" value={result.metrics.profit_factor?.toFixed(2) ?? "inf"} />
              <Metric label="Max drawdown" value={`${result.metrics.max_drawdown_pct.toFixed(1)}%`} />
              <Metric label="Final equity" value={fmtMoney(result.metrics.final_equity)} />
              {result.metrics.use_resistance_filter && (
                <Metric label="Blocked by resistance" value={result.metrics.n_skipped_resistance} />
              )}
              {result.metrics.use_ltf_bias && (
                <Metric
                  label={`${result.metrics.ltf_bias_timeframe} bias skipped`}
                  value={result.metrics.n_skipped_ltf_bias}
                />
              )}
            </div>

            <EquityCurve equity={result.metrics.equity_curve} />

            <div className="trade-log-header">
              <h3>Trade log</h3>
              <div className="symtabs-row">
                <button
                  className={"chip" + (yearFilter === "all" ? " chip-active" : "")}
                  onClick={() => setYearFilter("all")}
                >
                  All years
                </button>
                {availableYears.map((y) => (
                  <button
                    key={y}
                    className={"chip" + (yearFilter === y ? " chip-active" : "")}
                    onClick={() => setYearFilter(y)}
                  >
                    {y}
                  </button>
                ))}
              </div>
            </div>
            {periodSummary && (
              <p className="hint" style={{ marginTop: -6 }}>
                {yearFilter}: {periodSummary.n} trades · {periodSummary.winPct.toFixed(1)}% win · avgR{" "}
                {periodSummary.avgR >= 0 ? "+" : ""}{periodSummary.avgR.toFixed(3)} · PF{" "}
                {periodSummary.pf != null ? periodSummary.pf.toFixed(2) : "inf"} · total{" "}
                {fmtMoney(periodSummary.totalPnl)} (equity/drawdown above are full-history, not re-based per year)
              </p>
            )}
            <div className="trade-table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th></th><th>#</th><th>Dir</th><th>Entry time</th><th>Entry</th>
                    <th>Exit</th><th>R</th><th>{"P&L"}</th><th>Exit reason</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredTrades.map(({ t, originalIndex: i }) => (
                    <tr key={i} className={i === selectedIdx ? "row-selected" : ""} onClick={() => setSelectedIdx(i)}>
                      <td><input type="radio" readOnly checked={i === selectedIdx} /></td>
                      <td>{i + 1}</td>
                      <td>{t.direction === "short" ? "Short" : "Long"}</td>
                      <td>{new Date(t.entry_time * 1000).toISOString().replace("T", " ").slice(0, 16)}</td>
                      <td>{t.entry_price.toFixed(decimals)}</td>
                      <td>{t.exit_price.toFixed(decimals)}</td>
                      <td className={t.r_multiple > 0 ? "pos" : "neg"}>{t.r_multiple >= 0 ? "+" : ""}{t.r_multiple.toFixed(2)}</td>
                      <td className={t.pnl > 0 ? "pos" : "neg"}>{fmtMoney(t.pnl)}</td>
                      <td>{t.exit_reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {seasonality && (
              <>
                <h3>Seasonality (by calendar month{yearFilter === "all" ? ", all years combined" : `, ${yearFilter} only`})</h3>
                <p className="hint">
                  {yearFilter === "all"
                    ? "Every trade grouped by its entry month regardless of year -- a real edge should broadly survive across different years landing in the same month. Small per-month counts mean noisy R-multiples, so treat a single strong or weak month as a lead to investigate, not a rule to trade on by itself."
                    : `Only ${yearFilter}'s trades, grouped by month -- with a single year's worth of data most months have 0-3 trades, so treat this as an anecdote, not a pattern. Switch to "All years" for the statistically meaningful view.`}
                </p>
                <div className="seasonality-grid">
                  {seasonality.map((m) => (
                    <div
                      key={m.month}
                      className={
                        "season-cell" +
                        (m.count === 0 ? " season-empty" : m.avgR > 0 ? " season-pos" : " season-neg") +
                        (bestMonth && m.month === bestMonth.month && m.count > 0 ? " season-best" : "") +
                        (worstMonth && m.month === worstMonth.month && m.count > 0 && activeMonths.length > 1 ? " season-worst" : "")
                      }
                    >
                      <div className="season-month">{m.month}</div>
                      {m.count > 0 ? (
                        <>
                          <div className="season-r">{m.avgR >= 0 ? "+" : ""}{m.avgR.toFixed(2)}R</div>
                          <div className="season-sub">{m.count} trades · {m.winPct.toFixed(0)}% win</div>
                          <div className="season-sub">{fmtMoney(m.totalPnl)}</div>
                        </>
                      ) : (
                        <div className="season-sub">no trades</div>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}

            <MonteCarloPanel
              trades={filteredTrades.map(({ t }) => t)}
              riskPct={riskPct}
              startEquity={startEquity}
              note={
                <>
                  Bootstrap: draws this run's own trades at random, with replacement, into 1,000
                  alternative orderings (fixed seed 42) to show how much of the equity curve was
                  sequence luck. Each trade is resampled individually -- this strategy holds one
                  position at a time, so there are no overlapping blocks to preserve.
                  {sizingMode === "fixed_units" && (
                    <> <strong>Note:</strong> the simulation compounds a fixed % of equity per trade,
                    so it does not reflect the fixed-lot run shown above -- switch sizing to % risk
                    for these numbers to match.</>
                  )}
                  {yearFilter !== "all" && <> Currently resampling {yearFilter} only.</>}
                  {" "}It assumes trades are independent and identically distributed, which understates
                  risk if losses genuinely cluster. It is a re-shuffle of the same history, not
                  out-of-sample validation.
                </>
              }
            />

            <h3>Chart</h3>
            <div className="symtabs-row" style={{ marginBottom: 10 }}>
              {["D1", "M15"].map((tf) => (
                <button key={tf} className={"chip" + (chartTf === tf ? " chip-active" : "")} onClick={() => setChartTf(tf)}>
                  {tf}
                </button>
              ))}
            </div>
            <DailyFvgReplay
              candles={chartTf === "D1" ? result.candles : result.candles_m15}
              trade={selectedTrade}
              decimals={decimals}
              symbol={symbol}
              timeframe={chartTf}
            />
          </>
        )}
      </main>
    </div>
  );
}
