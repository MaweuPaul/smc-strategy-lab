"""
RESULT: DO NOT SHIP AS A FILTER. Structure bias does carry real signal --
on Gold, trades taken in bullish structure run 60.7% win / +0.794R vs
48.8% win / +0.391R in bearish structure -- but filtering on it loses
money anyway, for three reasons:

  1. Structure flips too late. In the Jan-Feb 2026 Gold selloff, three of
     the five consecutive losses fired while structure still read
     confirmed bullish; the first leg down off a top always precedes
     LH+LL. The filter blocked only 2 of those 5 losses.
  2. Bearish-structure trades are still PROFITABLE (+0.391R). Filtering
     them removes a winning subset, just a less-winning one -- and it
     also removed real winners (e.g. Gold 2026-07-24, +1.98R).
  3. Net on Gold: PF 2.37 -> 1.95, $241k -> $170k final. Only US30
     improved clearly (+0.081 -> +0.210R, DD -28.2% -> -11.2%); 1 of 8
     symbols is about what noise produces.

Also worth recording: the loss clustering that motivated this is largely
chance. Monte-Carlo shuffling Gold's own 103 trade outcomes into random
order gives a mean longest losing streak of 5.2 and produces a run of 7+
15.2% of the time. The observed streak of 7 sits at the ~90th percentile
-- notable, but well inside what a 55% win-rate system does unaided.

The live idea this leaves open is SIZING rather than filtering: keep the
bearish-structure trades but at reduced size, since they're positive but
weaker. Not yet tested.

Market-structure filter for the long-only daily FVG retracement strategy.

Observation this tests (from reading the chart, not from the numbers):
the strategy's consecutive-loss clusters line up with stretches where D1
market structure had flipped bearish -- a run of lower highs / lower lows
after a bearish BOS, before any bullish CHoCH. The strategy is long-only,
so during those stretches it is buying dips in a downtrend, over and over.

This is deliberately NOT the same test as the 200D SMA filter in
daily_fvg_winrate_levers.py, which mostly failed. An SMA is slow and
lagging: in a sharp selloff off a prior rally, price can sit above a
still-rising 200D average for weeks, so that filter never blocks the
trades that hurt. Market structure flips on the first confirmed LH+LL,
which is much closer to what a discretionary trader actually reads.

Uses smc_backtest.structure_bias_series, which is already non-repainting
(a swing at index s is only usable from s+swing_window onward). Bias is
read on the TRIGGER day -- known at that day's close -- while the entry
is still the next day's open, so nothing leaks.

Variants:
  none        -- baseline, take every retracement buy
  not-bearish -- skip only when structure is confirmed bearish
  bullish     -- strictest, require confirmed bullish structure
"""


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # repo root, for shared modules (smc_backtest, etc.)

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from smc_backtest import find_swings, structure_bias_series
from daily_fvg_newday import fetch, find_entries, simulate, START, END
from p404_sweep_reversal import compute_atr14
import daily_fvg_newday as fvg_mod

SYMBOLS = ["XAUUSD", "XAGUSD", "USTEC", "US30", "US500", "USDJPY", "GBPUSD", "EURUSD"]
METALS = ["XAUUSD", "XAGUSD"]


def rr_for(s):
    return 3.0 if s == "XAGUSD" else 2.0


def buf_for(s):
    if s in METALS:
        return 1.0
    if s == "USDJPY":
        return 0.75
    return 0.25


def structure_bias_for(d1, swing_window=2):
    sh, sl = find_swings(d1, window=swing_window)
    return structure_bias_series(d1, sh, sl, swing_window=swing_window)


def filter_by_structure(entries, bias, mode):
    """mode: 'not-bearish' or 'bullish'. Bias read on the trigger day."""
    kept = []
    for idx, gap in entries:
        day = gap.get("_entered_day_idx")
        if day is None:
            continue
        b = bias[day]
        if mode == "not-bearish" and b == "bearish":
            continue
        if mode == "bullish" and b != "bullish":
            continue
        kept.append((idx, gap))
    return kept


def stats(trades, curve):
    if len(trades) == 0:
        return None
    v = np.asarray(curve)
    peak = np.maximum.accumulate(v)
    profit = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    loss = -trades.loc[trades["pnl"] < 0, "pnl"].sum()
    # worst run of consecutive losers -- the thing the chart observation is about
    worst_streak = cur = 0
    for p in trades["pnl"]:
        cur = cur + 1 if p < 0 else 0
        worst_streak = max(worst_streak, cur)
    return {
        "n": len(trades), "win": 100 * (trades["pnl"] > 0).mean(),
        "avgR": trades["r_multiple"].mean(),
        "pf": profit / loss if loss else float("nan"),
        "dd": ((v - peak) / peak).min() * 100,
        "final": curve[-1], "streak": worst_streak,
    }


def main(swing_window=2):
    print(f"Structure filter (swing_window={swing_window}) -- long-only daily FVG retracement\n")
    for symbol in SYMBOLS:
        d1 = fetch(symbol, mt5.TIMEFRAME_D1, START, END)
        m15 = fetch(symbol, mt5.TIMEFRAME_M15, START, END)
        atr = compute_atr14(d1).values
        fvg_mod.ATR_BUFFER_MULT = buf_for(symbol)
        fvg_mod.TARGET_RR = rr_for(symbol)
        fvg_mod.SIZING_MODE = "risk_pct"

        base = find_entries(d1, m15)
        for _, g in base:
            g["_atr"] = atr[g["formed_at"]] if not np.isnan(atr[g["formed_at"]]) else 0.0
        bias = structure_bias_for(d1, swing_window)

        print(f"=== {symbol} (RR={rr_for(symbol)}, buf={buf_for(symbol)}) ===")
        print(f"{'filter':>12} {'n':>4} {'win%':>6} {'avgR':>7} {'PF':>5} {'DD%':>7} "
              f"{'maxLossRun':>10} {'final':>10}")
        for mode in ("none", "not-bearish", "bullish"):
            entries = base if mode == "none" else filter_by_structure(base, bias, mode)
            tr, curve = simulate(m15, entries)
            s = stats(tr, curve)
            if s is None:
                print(f"{mode:>12}    no trades")
                continue
            print(f"{mode:>12} {s['n']:4d} {s['win']:6.1f} {s['avgR']:+7.3f} {s['pf']:5.2f} "
                  f"{s['dd']:7.1f} {s['streak']:10d} {s['final']:10,.0f}")
        print()


if __name__ == "__main__":
    main()
