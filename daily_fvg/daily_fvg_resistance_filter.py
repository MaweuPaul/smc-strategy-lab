"""
Extends daily_fvg_newday.py with a resistance-awareness filter: skip a
bullish FVG buy signal if a prior, still-unbroken D1 swing high sits
between entry and the target -- i.e. real overhead resistance would have
to be broken before the target could ever be reached, which is exactly
the visual pattern a discretionary trader would skip (or fade) by eye.

Filter rule (checked on the TRIGGER day, before execution -- the actual
entry/stop/target are still computed properly on the real M15 fill;
this only uses the trigger day's D1 close as an estimate for screening):

  last_swing_high = most recent CONFIRMED D1 swing high (5-bar fractal,
                     non-repainting) as of the trigger day's close.
  entry_est        = trigger day's D1 close (a same-day-close stand-in
                     for the next day's open -- usually very close).
  reward_est        = TARGET_RR x (entry_est - stop)

  SKIP the trade if: last_swing_high > entry_est (i.e. it's still above
  price, unbroken) AND (last_swing_high - entry_est) < reward_est (i.e.
  it sits closer than the target -- real resistance in the way).

Nothing else changes from daily_fvg_newday.py.
"""


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # repo root, for shared modules (smc_backtest, etc.)

import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime

from discount_breakout_backtest import compute_confirmed_swings
from daily_fvg_newday import fetch, find_entries, simulate, summarize, SYMBOLS, START, END
from p404_sweep_reversal import compute_atr14
import daily_fvg_newday as fvg_mod

SWING_N = 5


def filter_by_resistance(d1, entries):
    sh, _ = compute_confirmed_swings(d1, n=SWING_N)
    d1_close = d1["close"].values
    kept, skipped = [], 0
    for entry_idx, gap in entries:
        trigger_day = gap["_entered_day_idx"]
        entry_est = d1_close[trigger_day]
        stop = gap["bottom"] - fvg_mod.ATR_BUFFER_MULT * gap.get("_atr", 0.0)
        r_est = entry_est - stop
        if r_est <= 0:
            kept.append((entry_idx, gap))
            continue
        reward_est = fvg_mod.TARGET_RR * r_est
        last_sh = sh[trigger_day]
        if not np.isnan(last_sh) and last_sh > entry_est and (last_sh - entry_est) < reward_est:
            skipped += 1
            continue
        kept.append((entry_idx, gap))
    return kept, skipped


def main():
    for symbol in SYMBOLS:
        print(f"\nFetching {symbol} D1 + M15 {START.date()} -> {END.date()} ...")
        d1 = fetch(symbol, mt5.TIMEFRAME_D1, START, END)
        m15 = fetch(symbol, mt5.TIMEFRAME_M15, START, END)

        atr = compute_atr14(d1).values
        entries = find_entries(d1, m15)
        for _, gap in entries:
            gap["_atr"] = atr[gap["formed_at"]] if not np.isnan(atr[gap["formed_at"]]) else 0.0

        filtered, n_skipped = filter_by_resistance(d1, entries)
        print(f"  entries: {len(entries)}  skipped (blocked by resistance): {n_skipped}  remaining: {len(filtered)}")

        trades, curve = simulate(m15, filtered)
        summarize(trades, curve, symbol)


if __name__ == "__main__":
    main()
