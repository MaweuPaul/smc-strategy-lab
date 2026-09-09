"""
Win-rate levers for the (clean, long-only) daily FVG retracement strategy.

Win rate on its own is not the objective -- it trades off directly against
target distance, so a lower RR mechanically buys a higher win rate while
shrinking each win. This sweeps the two levers that actually matter and
prints them side by side so the tradeoff is visible rather than implied:

  1. TARGET_RR       -- the mechanical tradeoff (more wins, smaller wins).
  2. SMA200 trend filter -- only buy when the trigger day closed above its
     own 200-day SMA. Non-repainting: the SMA and the close both come from
     the trigger day, which is known at that day's close, and the entry is
     still the next day's open. This is a genuine filter (it should raise
     win rate AND expectancy if trend alignment carries real signal),
     unlike lowering RR which only redistributes the same edge.

Motivation for the trend filter: these instruments spend most of their
time above the 200D SMA (USTEC 81%, Gold 76%, Silver 68%), and the
mirrored bearish/short version of this strategy lost on every symbol --
both point at the edge being specifically a "buy dips in an uptrend"
pattern rather than a symmetric FVG effect.
"""

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from daily_fvg_newday import fetch, find_entries, simulate, START, END
from p404_sweep_reversal import compute_atr14
import daily_fvg_newday as fvg_mod

SYMBOLS = ["XAUUSD", "XAGUSD", "USTEC", "US30", "US500", "USDJPY"]
RR_GRID = [1.0, 1.5, 2.0, 3.0]
METALS = ["XAUUSD", "XAGUSD"]


def buf_for(s):
    if s in METALS:
        return 1.0
    if s == "USDJPY":
        return 0.75
    return 0.25


def filter_by_trend(d1, entries):
    """Keep only entries whose trigger day closed above its 200D SMA."""
    sma200 = d1["close"].rolling(200).mean().values
    close = d1["close"].values
    kept = []
    for idx, gap in entries:
        day = gap.get("_entered_day_idx")
        if day is None or np.isnan(sma200[day]):
            continue
        if close[day] > sma200[day]:
            kept.append((idx, gap))
    return kept


def stats(trades, curve):
    if len(trades) == 0:
        return None
    v = np.asarray(curve)
    peak = np.maximum.accumulate(v)
    profit = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    loss = -trades.loc[trades["pnl"] < 0, "pnl"].sum()
    return {
        "n": len(trades),
        "win": 100 * (trades["pnl"] > 0).mean(),
        "avgR": trades["r_multiple"].mean(),
        "pf": profit / loss if loss else float("nan"),
        "dd": ((v - peak) / peak).min() * 100,
        "final": curve[-1],
    }


def main():
    for symbol in SYMBOLS:
        d1 = fetch(symbol, mt5.TIMEFRAME_D1, START, END)
        m15 = fetch(symbol, mt5.TIMEFRAME_M15, START, END)
        atr = compute_atr14(d1).values
        fvg_mod.ATR_BUFFER_MULT = buf_for(symbol)
        fvg_mod.SIZING_MODE = "risk_pct"

        base = find_entries(d1, m15)
        for _, g in base:
            g["_atr"] = atr[g["formed_at"]] if not np.isnan(atr[g["formed_at"]]) else 0.0
        trend = filter_by_trend(d1, base)

        print(f"\n=== {symbol} (buf={buf_for(symbol)}) "
              f"-- {len(base)} entries, {len(trend)} above SMA200 ===")
        print(f"{'RR':>4} {'filter':>10} {'n':>4} {'win%':>6} {'avgR':>7} {'PF':>5} {'DD%':>7} {'final':>10}")
        for rr in RR_GRID:
            fvg_mod.TARGET_RR = rr
            for label, entries in (("none", base), ("SMA200", trend)):
                tr, curve = simulate(m15, entries)
                s = stats(tr, curve)
                if s is None:
                    continue
                print(f"{rr:4.1f} {label:>10} {s['n']:4d} {s['win']:6.1f} "
                      f"{s['avgR']:+7.3f} {s['pf']:5.2f} {s['dd']:7.1f} {s['final']:10,.0f}")


if __name__ == "__main__":
    main()
