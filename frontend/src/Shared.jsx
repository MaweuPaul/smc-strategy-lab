import { useRef, useState } from "react";

export const ALL_ALIASES = { USTEC: "NAS100", XAGUSD: "Silver", XAUUSD: "Gold" };

export function fmtMoney(v) {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}

/**
 * Trades and the equity curve are both ordered by entry_time, and the curve
 * has one more point than there are trades (starting balance first). So
 * equity[i] is the balance BEFORE trade i, equity[i+1] is the balance after.
 * Returns { riskAmount, pnlAmount } in dollars for the trade at that index.
 */
export function tradeMoney(trades, equityCurve, riskPct, index) {
  const row = trades[index];
  if (row && Number.isFinite(row.risk_amount) && Number.isFinite(row.pnl_amount)) {
    return { riskAmount: row.risk_amount, pnlAmount: row.pnl_amount, balanceAfter: row.balance_after };
  }
  if (!equityCurve || index >= equityCurve.length - 1) return { riskAmount: null, pnlAmount: null, balanceAfter: null };
  const before = equityCurve[index];
  const after = equityCurve[index + 1];
  const riskMult = trades[index]?.risk_mult ?? 1;
  return { riskAmount: before * (riskPct / 100) * riskMult, pnlAmount: after - before, balanceAfter: after };
}

export function Metric({ label, value }) {
  return (
    <div className="metric">
      <div className="metric-value">{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}

export function EquityCurve({ equity, times }) {
  const svgRef = useRef(null);
  const [hoverIdx, setHoverIdx] = useState(null);
  const [logScale, setLogScale] = useState(false);
  if (!equity || equity.length < 2) return null;

  const w = 900, h = 200, pad = 10;
  const canLog = equity.every((v) => v > 0);
  const useLog = logScale && canLog;
  const scaled = useLog ? equity.map((v) => Math.log(v)) : equity;
  const min = Math.min(...scaled), max = Math.max(...scaled);
  const x = (i) => pad + (i / (equity.length - 1)) * (w - 2 * pad);
  const y = (v) => h - pad - (((useLog ? Math.log(v) : v) - min) / (max - min || 1)) * (h - 2 * pad);
  const path = equity.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(v)}`).join(" ");

  function handleMove(e) {
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const relX = ((e.clientX - rect.left) / rect.width) * w;
    const frac = (relX - pad) / (w - 2 * pad);
    const idx = Math.round(frac * (equity.length - 1));
    setHoverIdx(Math.min(equity.length - 1, Math.max(0, idx)));
  }

  const hover = hoverIdx != null && hoverIdx < equity.length ? { idx: hoverIdx, value: equity[hoverIdx], x: x(hoverIdx), y: y(equity[hoverIdx]) } : null;
  const labelRight = hover && hover.x > w - 160;

  return (
    <div>
      <label className="checkbox-row" style={{ fontSize: 12, marginBottom: 4 }}>
        <input type="checkbox" checked={useLog} disabled={!canLog}
               onChange={(e) => setLogScale(e.target.checked)} />
        Log scale (shows whether growth was actually steady, or accelerating)
      </label>
      {times && <p className="hint">Realized balance at settlement events; open P&amp;L is excluded.</p>}
      <svg
        ref={svgRef}
        viewBox={`0 0 ${w} ${h}`}
        className="equity-curve"
        onMouseMove={handleMove}
        onMouseLeave={() => setHoverIdx(null)}
      >
        <path d={path} fill="none" stroke="#5b8def" strokeWidth="2" />
        {hover && (
          <g pointerEvents="none">
            <line x1={hover.x} y1={pad} x2={hover.x} y2={h - pad} stroke="#5b8def" strokeWidth="1" strokeDasharray="4 3" opacity="0.6" />
            <circle cx={hover.x} cy={hover.y} r="4" fill="#5b8def" />
            <g transform={`translate(${labelRight ? hover.x - 150 : hover.x + 8}, ${Math.max(pad, hover.y - 28)})`}>
              <rect width="145" height="34" rx="4" fill="#1a1f2e" stroke="#5b8def" strokeWidth="1" opacity="0.95" />
              <text x="8" y="14" fontSize="11" fill="#e8eaf0">{hover.idx === 0 ? "Start" : (times ? `Settlement #${hover.idx}` : `Trade #${hover.idx}`)}</text>
              <text x="8" y="27" fontSize="12" fill="#5b8def" fontWeight="bold">{fmtMoney(hover.value)}</text>
            </g>
          </g>
        )}
      </svg>
    </div>
  );
}

export function CapitalInput({ value, onChange }) {
  return (
    <>
      <label>Starting capital ($)</label>
      <input
        type="number"
        min={1}
        step={100}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </>
  );
}

export function SymbolPicker({ symbolsList, symbols, toggleSymbol }) {
  return (
    <div className="symbol-grid">
      {symbolsList.map((s) => (
        <button
          key={s}
          className={"chip" + (symbols.includes(s) ? " chip-active" : "")}
          onClick={() => toggleSymbol(s)}
          title={ALL_ALIASES[s] || s}
        >
          {s}
          {ALL_ALIASES[s] && <span className="alias"> ({ALL_ALIASES[s]})</span>}
        </button>
      ))}
    </div>
  );
}
