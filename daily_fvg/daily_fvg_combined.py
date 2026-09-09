"""
Combines both daily FVG entry styles instead of picking one:
  - continuation: buy right after the gap confirms (no wait)
  - retracement: buy at the next day's open once price later pulls back
    into the SAME gap (if it ever does)

Both come from independent find_fvgs() calls (daily_fvg_newday.find_entries
and daily_fvg_continuation.find_continuation_entries), so a single gap can
legitimately produce both an immediate continuation entry AND a later
retracement entry if price comes back to it -- that's the point: don't
just sit flat waiting for the retracement, take the continuation trade in
the meantime, and still take the retracement later if it happens.

The two entry lists are merged and time-sorted; the existing one-trade-
at-a-time logic in simulate() naturally skips a second entry if it would
start before the first one (whichever style) has already exited.
"""


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # repo root, for shared modules (smc_backtest, etc.)

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from daily_fvg_newday import fetch, find_entries, simulate, summarize, SYMBOLS, START, END
from daily_fvg_continuation import find_continuation_entries
from p404_sweep_reversal import compute_atr14
import daily_fvg_newday as fvg_mod


def find_combined_entries(d1, m15):
    retracement = find_entries(d1, m15)
    continuation = find_continuation_entries(d1, m15)
    combined = retracement + [(idx, gap) for idx, gap in continuation]
    combined.sort(key=lambda e: e[0])
    return combined, len(retracement), len(continuation)


def main():
    for symbol in SYMBOLS:
        print(f"\nFetching {symbol} D1 + M15 {START.date()} -> {END.date()} ...")
        d1 = fetch(symbol, mt5.TIMEFRAME_D1, START, END)
        m15 = fetch(symbol, mt5.TIMEFRAME_M15, START, END)

        atr = compute_atr14(d1).values
        combined, n_retr, n_cont = find_combined_entries(d1, m15)
        for _, gap in combined:
            gap["_atr"] = atr[gap["formed_at"]] if not np.isnan(atr[gap["formed_at"]]) else 0.0
        print(f"  retracement candidates: {n_retr}  continuation candidates: {n_cont}  combined pool: {len(combined)}")

        trades, curve = simulate(m15, combined)
        summarize(trades, curve, f"{symbol} (combined)")


if __name__ == "__main__":
    main()
