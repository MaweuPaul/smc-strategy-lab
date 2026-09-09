import { useEffect, useState } from "react";
import { fmtMoney } from "./Shared";

import { runMonteCarlo } from "./dailyObAnalysis";

function SpaghettiChart({ curves }) {
  if (!curves || curves.length === 0) return null;
  const w = 900, h = 260, pad = 10;
  const allVals = curves.flat();
  const min = allVals.reduce((a,b) => Math.min(a,b), Infinity), max = allVals.reduce((a,b) => Math.max(a,b), -Infinity);
  const len = curves[0].length;
  const x = (i) => pad + (i / (len - 1)) * (w - 2 * pad);
  const y = (v) => h - pad - ((v - min) / (max - min || 1)) * (h - 2 * pad);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="equity-curve" style={{ height: 260 }}>
      {curves.map((c, idx) => (
        <path
          key={idx}
          d={c.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(v)}`).join(" ")}
          fill="none"
          stroke="#5b8def"
          strokeWidth="1"
          opacity="0.15"
        />
      ))}
    </svg>
  );
}

export default function MonteCarloPanel({ trades, riskPct, startEquity, note }) {
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);

  useEffect(() => { setResult(null); }, [trades, riskPct, startEquity]);

  function handleRun() {
    setRunning(true);
    setTimeout(() => {
      setResult(runMonteCarlo(trades, riskPct, startEquity, 1000));
      setRunning(false);
    }, 10);
  }

  return (
    <div className="monte-carlo">
      <h3>Monte Carlo (bootstrap resample)</h3>
      <p className="hint">
        {note ?? (
          <>
            Resamples the saved run with a fixed seed (42). Daily OB resamples complete
            overlapping-exposure blocks, preserving base/add legs and concurrent trades within
            each block. Entries use realized cash. Dependence between blocks and trading costs
            are not modeled; this is not out-of-sample validation.
          </>
        )}
      </p>
      <button className="run-btn" onClick={handleRun} disabled={running || trades.length === 0}
              style={{ maxWidth: 260 }}>
        {running ? "Running 1,000 sims…" : "Run Monte Carlo"}
      </button>

      {result && (
        <>
          <SpaghettiChart curves={result.sampleCurves} />
          <div className="metrics-row">
            <div className="metric">
              <div className="metric-value">{fmtMoney(result.finalEquity.p50)}</div>
              <div className="metric-label">Median final equity</div>
            </div>
            <div className="metric">
              <div className="metric-value">{fmtMoney(result.finalEquity.p5)}</div>
              <div className="metric-label">Worst 5% final equity</div>
            </div>
            <div className="metric">
              <div className="metric-value">{fmtMoney(result.finalEquity.p95)}</div>
              <div className="metric-label">Best 5% final equity</div>
            </div>
            <div className="metric">
              <div className="metric-value">{result.maxDrawdown.p50.toFixed(1)}%</div>
              <div className="metric-label">Median max drawdown</div>
            </div>
            <div className="metric">
              <div className="metric-value">{result.maxDrawdown.p5.toFixed(1)}%</div>
              <div className="metric-label">Worst 5% max drawdown</div>
            </div>
          </div>
          <p className="hint">
            Across 1,000 resampled sequences: {result.probDrawdownWorse[30].toFixed(1)}% of runs saw
            a drawdown worse than -30%, and {result.probDrawdownWorse[50].toFixed(1)}% saw worse
            than -50%. These estimates depend on the historical sample and bootstrap assumptions.
          </p>
        </>
      )}
    </div>
  );
}
