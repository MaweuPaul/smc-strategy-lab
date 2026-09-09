"""
Daily FVG CONTINUATION variant -- the opposite entry timing from
daily_fvg_newday.py's retracement version.

Retracement (existing): wait for price to trade BACK INTO the bullish
  gap (a pullback/dip entry), buy at the next day's open after that.
Continuation (this file): buy at the OPEN of the day right after the
  bullish gap CONFIRMS (formed_at's next D1 bar) -- no waiting for a
  pullback. This bets the displacement that created the gap keeps going,
  rather than betting on a retest-and-bounce.

Everything else is identical to daily_fvg_newday.py: same stop (gap
bottom - ATR_BUFFER_MULT x D1 ATR14 as of the day the gap formed), same
fixed-RR single target, same one-trade-at-a-time M15 execution, same
long-only (bullish gaps only) scope.

Note the stop is now measured from much farther away in most cases (the
entry sits well above the gap after a day or more of continuation, while
the stop is still down at the gap's bottom) -- kept identical to the
retracement version deliberately, for a fair side-by-side comparison, not
because it's necessarily the "right" stop for this different entry style.
"""


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # repo root, for shared modules (smc_backtest, etc.)

import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime

from smc_backtest import find_fvgs
from daily_fvg_newday import fetch, simulate, summarize, SYMBOLS, START, END
import daily_fvg_newday as fvg_mod
from p404_sweep_reversal import compute_atr14


def find_continuation_entries(d1, m15):
    """Returns list of (m15_idx, gap) -- one per bullish FVG, firing at
    the open of the D1 bar immediately after formed_at. No retracement
    wait, no inversion check (there's nothing to invalidate yet -- we're
    entering before price ever comes back down to the gap)."""
    fvgs = find_fvgs(d1)
    only_bullish = [g for g in fvgs if g["type"] == "bullish"]
    d1_date = d1["time"].dt.date.values
    m15_date = m15["time"].dt.date.values
    n = len(d1)

    first_m15_idx_for_date = {}
    for i, dt in enumerate(m15_date):
        if dt not in first_m15_idx_for_date:
            first_m15_idx_for_date[dt] = i

    entries = []
    for gap in only_bullish:
        next_idx = gap["formed_at"] + 1
        if next_idx >= n:
            continue
        next_date = d1_date[next_idx]
        if next_date not in first_m15_idx_for_date:
            continue
        entries.append((first_m15_idx_for_date[next_date], gap))
    return sorted(entries, key=lambda e: e[0])


def main():
    for symbol in SYMBOLS:
        print(f"\nFetching {symbol} D1 + M15 {START.date()} -> {END.date()} ...")
        d1 = fetch(symbol, mt5.TIMEFRAME_D1, START, END)
        m15 = fetch(symbol, mt5.TIMEFRAME_M15, START, END)

        atr = compute_atr14(d1).values
        entries = find_continuation_entries(d1, m15)
        for _, gap in entries:
            gap["_atr"] = atr[gap["formed_at"]] if not np.isnan(atr[gap["formed_at"]]) else 0.0
        print(f"  continuation entries: {len(entries)}")

        trades, curve = simulate(m15, entries)
        summarize(trades, curve, f"{symbol} (continuation)")


if __name__ == "__main__":
    main()
