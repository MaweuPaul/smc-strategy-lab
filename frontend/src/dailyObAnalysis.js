// Pure accounting/replay helpers: covered by node:test without a browser.
export const TF_SECONDS = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400 };

export function eventIndex(candles, isoTime, timeframe, atClose = false) {
  if (!isoTime || candles.length === 0) return null;
  const instant = new Date(isoTime).getTime() / 1000;
  if (!Number.isFinite(instant)) return null;
  const target = instant - (atClose ? 0.001 : 0);
  const duration = TF_SECONDS[timeframe];
  const index = candles.findIndex(c => c.time <= target && target < c.time + duration);
  return index < 0 ? null : index;
}

export function exposureBlocks(trades) {
  const sorted = [...trades].sort((a, b) => Date.parse(a.entry_time) - Date.parse(b.entry_time));
  const blocks = [];
  for (const trade of sorted) {
    const start = Date.parse(trade.entry_time), end = Date.parse(trade.exit_time);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) continue;
    let block = blocks.at(-1);
    if (!block || start >= block.end) {
      block = { start, end, trades: [] };
      blocks.push(block);
    }
    block.end = Math.max(block.end, end);
    block.trades.push(trade);
  }
  return blocks;
}

export function settleBlock(block, balance, riskPct) {
  const events = new Map(), pnl = new Map();
  block.trades.forEach((t, i) => {
    for (const [key, timestamp] of [['entry', t.entry_time], ['exit', t.exit_time]]) {
      const time = Date.parse(timestamp);
      if (!events.has(time)) events.set(time, { entry: [], exit: [] });
      events.get(time)[key].push(i);
    }
  });
  const balances = [];
  for (const [, event] of [...events].sort((a, b) => a[0] - b[0])) {
    const prior = event.exit.filter(i => !event.entry.includes(i));
    const same = event.exit.filter(i => event.entry.includes(i));
    if (prior.length) {
      balance += prior.reduce((sum, i) => sum + pnl.get(i), 0);
      balances.push(balance);
    }
    for (const i of event.entry) {
      const t = block.trades[i];
      pnl.set(i, Math.max(balance, 0) * riskPct / 100 * (t.risk_mult ?? 1) * t.r_multiple);
    }
    if (same.length) {
      balance += same.reduce((sum, i) => sum + pnl.get(i), 0);
      balances.push(balance);
    }
  }
  return { balance, balances };
}

export function runMonteCarlo(trades, riskPct, startEquity, numSims = 1000, curvesToKeep = 40, seed = 42) {
  const pool = trades.filter(t => Number.isFinite(t.r_multiple) && Number.isFinite(t.risk_mult ?? 1));
  if (!pool.length) return null;
  const grouped = pool.every(t => t.chain_id && t.entry_time && t.exit_time);
  const blocks = grouped ? exposureBlocks(pool) : pool.map(t => ({ trades: [t] }));
  if (!blocks.length) return null;
  let state = seed >>> 0;
  const random = () => { state = (Math.imul(1664525, state) + 1013904223) >>> 0; return state / 4294967296; };
  const finalEquities = [], maxDrawdowns = [], sampleCurves = [];
  const keepEvery = Math.max(1, Math.floor(numSims / curvesToKeep));
  for (let s = 0; s < numSims; s++) {
    let equity = startEquity, peak = startEquity, maxDD = 0;
    const curve = [equity];
    for (let i = 0; i < blocks.length; i++) {
      const pick = blocks[Math.floor(random() * blocks.length)];
      const path = grouped ? settleBlock(pick, equity, riskPct).balances
        : [equity + Math.max(equity, 0) * riskPct / 100 * (pick.trades[0].risk_mult ?? 1) * pick.trades[0].r_multiple];
      for (const value of path) {
        equity = value;
        peak = Math.max(peak, equity);
        maxDD = Math.min(maxDD, (equity - peak) / peak * 100);
      }
      curve.push(equity);
    }
    finalEquities.push(equity); maxDrawdowns.push(maxDD);
    if (s % keepEvery === 0) sampleCurves.push(curve);
  }
  const pct = (arr, p) => arr[Math.min(arr.length - 1, Math.floor(p * arr.length))];
  finalEquities.sort((a,b) => a-b); maxDrawdowns.sort((a,b) => a-b);
  const quantiles = arr => ({ p5: pct(arr,.05), p50: pct(arr,.5), p95: pct(arr,.95) });
  return { sampleCurves, finalEquity: quantiles(finalEquities), maxDrawdown: quantiles(maxDrawdowns),
    probDrawdownWorse: { 30: maxDrawdowns.filter(d => d <= -30).length / numSims * 100,
                        50: maxDrawdowns.filter(d => d <= -50).length / numSims * 100 },
    numSims, n: pool.length, blocks: blocks.length, grouped, seed };
}
