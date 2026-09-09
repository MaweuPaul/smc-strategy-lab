from pathlib import Path
import shutil, re
root = Path(__file__).resolve().parent
backup = root / 'audit_backups' / 'daily_ob_20260907'

def edit(name, transform):
    path = root / name
    saved = backup / name
    if not saved.exists():
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, saved)
    path.write_text(transform(path.read_text(encoding='utf-8')), encoding='utf-8')

def panel(s):
    s = s.replace('  const [loading, setLoading]', '  const [auditMode, setAuditMode] = useState(true);\n  const [loading, setLoading]')
    s = s.replace('Daily (D1) — higher conviction, fewer trades (best PF/drawdown)', 'Daily (D1)')
    s = s.replace('4-Hour (H4) — ~2.6x more trades, but thinner edge', '4-Hour (H4)')
    hints = [
      ('Tested on Gold:', 'D1 uses H1 entries; H4 uses M15 entries. Compare corrected runs over the same data period.'),
      ('On by default — tested strictly better', 'Monitoring starts only after both the OB breakout and its qualifying FVG have closed.'),
      ('On by default (v2 settings)', 'A confirmed structure break schedules a next-open exit for the OB chain. Grace bars delay monitoring.'),
      ('Tested: Off gives', 'Bias uses completed higher-timeframe candles only. Compare modes using the corrected engine.'),
      ("The add\'s stop sits", 'The add risks the selected fraction of normal risk. It opens only while the base is still active; structure invalidation applies to both legs.')]
    for prefix, replacement in hints:
        pattern = r'<p className="hint">\s*' + re.escape(prefix) + r'.*?</p>'
        s, n = re.subn(pattern, '<p className="hint">' + replacement + '</p>', s, count=1, flags=re.S)
        assert n == 1, prefix
    s = s.replace('Off — always trade continuation (best return)', 'Off — trade the OB direction')
    s = s.replace('Reverse — fade counter-trend OBs as breaker blocks (smoothest ride)', 'Reverse — reverse counter-trend OBs')
    s = s.replace('<h3>D1 trend bias</h3>', '<h3>Higher-timeframe trend bias</h3>')
    s = re.sub(r'<p className="subtitle">.*?</p>', '''<p className="subtitle">
          Wait for a confirmed OB and any required displacement, then enter on the next open
          after a retracement signal. Results use realized cash and gross prices, excluding
          spread, commissions, slippage and financing. Open-position drawdown is not included.
        </p>''', s, count=1, flags=re.S)
    s = s.replace('disabled={loading}>', 'disabled={loading || symbols.length === 0 || minRr > maxRr || startEquity <= 0}>')
    s = s.replace('        {!result &&', '''        {result && Object.keys(result.errors || {}).length > 0 && (
          <div className="error">Partial/failed run: {Object.entries(result.errors).map(([sym, error]) => `${sym}: ${error}`).join("; ")}</div>
        )}
        {result && <p className="hint">Run {result.run_id} · {result.engine_version}. Results use saved run settings; edited controls apply after Run backtest.</p>}
        {!result &&''')
    s = s.replace('label="Max drawdown"', 'label="Realized-balance drawdown"')
    s = s.replace('fmtMoney(startEquity * (riskPct / 100))', 'fmtMoney(result.config.start_equity * (result.config.risk_pct / 100))')
    s = re.sub(r'Over \{result.metrics.years_span.toFixed\(1\)\} years\..*?real trading does\)\.', '''Over {result.metrics.years_span.toFixed(1)} years of data coverage.
                CAGR annualizes the corrected realized balance; it does not account for
                trading costs or capacity limits. Per-symbol history can differ.''', s, count=1, flags=re.S)
    s = s.replace('<EquityCurve equity={result.metrics.equity_curve} />', '<EquityCurve equity={result.metrics.equity_curve} times={result.metrics.equity_times} />')
    s = s.replace('<MonteCarloPanel trades={result.trades} riskPct={riskPct} startEquity={startEquity} />', '<MonteCarloPanel key={result.run_id} trades={result.trades} riskPct={result.config.risk_pct} startEquity={result.config.start_equity} />')
    s = s.replace('            <h3>Trade log</h3>', '''            <h3>Trade log</h3>
            <label className="checkbox-row"><input type="checkbox" checked={auditMode} onChange={e => setAuditMode(e.target.checked)} />Hide individual outcomes until revealed in replay</label>''')
    s = s.replace('result.metrics.equity_curve, riskPct, i)', 'result.metrics.equity_curve, result.config.risk_pct, i)')
    s = s.replace('<td>{t.outcome}</td>', '<td>{auditMode ? "Hidden" : t.outcome}</td>')
    s = s.replace('className={t.r_multiple > 0 ? "pos" : "neg"}>{t.r_multiple?.toFixed(2)}', 'className={auditMode ? "" : t.r_multiple > 0 ? "pos" : "neg"}>{auditMode ? "—" : t.r_multiple?.toFixed(2)}')
    s = s.replace('className={pnlAmount > 0 ? "pos" : "neg"}>{fmtMoney(pnlAmount)}', 'className={auditMode ? "" : pnlAmount > 0 ? "pos" : "neg"}>{auditMode ? "—" : fmtMoney(pnlAmount)}')
    s = s.replace('<td>{fmtMoney(balanceAfter)}</td>', '<td>{auditMode ? "—" : fmtMoney(balanceAfter)}</td>')
    s = s.replace('<TradeReplay trade={selectedTrade} ltf={result.ltf} />', '<TradeReplay trade={selectedTrade} ltf={result.ltf} auditMode={auditMode} />')
    return s

def shared(s):
    s = s.replace('  if (!equityCurve || index >= equityCurve.length - 1)', '''  const row = trades[index];
  if (row && Number.isFinite(row.risk_amount) && Number.isFinite(row.pnl_amount)) {
    return { riskAmount: row.risk_amount, pnlAmount: row.pnl_amount, balanceAfter: row.balance_after };
  }
  if (!equityCurve || index >= equityCurve.length - 1)''')
    s = s.replace('EquityCurve({ equity })', 'EquityCurve({ equity, times })')
    s = s.replace('const hover = hoverIdx != null ?', 'const hover = hoverIdx != null && hoverIdx < equity.length ?')
    s = s.replace('`Trade #${hover.idx}`', '(times ? `Settlement #${hover.idx}` : `Trade #${hover.idx}`)')
    s = s.replace('      <svg\n', '      {times && <p className="hint">Realized balance at settlement events; open P&amp;L is excluded.</p>}\n      <svg\n', 1)
    return s

def monte(s):
    a, b = s.index('/**'), s.index('function SpaghettiChart')
    s = s[:a] + 'import { runMonteCarlo } from "./dailyObAnalysis";\n\n' + s[b:]
    s = s.replace('const min = Math.min(...allVals), max = Math.max(...allVals);', 'const min = allVals.reduce((a,b) => Math.min(a,b), Infinity), max = allVals.reduce((a,b) => Math.max(a,b), -Infinity);')
    s = s.replace('import { useState }', 'import { useEffect, useState }')
    s = s.replace('  function handleRun()', '  useEffect(() => { setResult(null); }, [trades, riskPct, startEquity]);\n\n  function handleRun()')
    s = re.sub(r'Reshuffles this strategy\'s.*?history happened to deliver\.', '''Resamples the saved run with a fixed seed (42). Daily OB resamples complete
        overlapping-exposure blocks, preserving base/add legs and concurrent trades within
        each block. Entries use realized cash. Dependence between blocks and trading costs
        are not modeled; this is not out-of-sample validation.''', s, count=1, flags=re.S)
    s = re.sub(r'If the historical run you saw above.*?as smooth\.', 'These estimates depend on the historical sample and bootstrap assumptions.', s, count=1, flags=re.S)
    return s

edit('frontend/src/DailyObPanel.jsx', panel)
edit('frontend/src/Shared.jsx', shared)
edit('frontend/src/MonteCarlo.jsx', monte)
print('Daily OB result snapshots, labels and Monte Carlo updated.')
