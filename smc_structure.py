"""
BOS / CHoCH market-structure bias, ported from the LuxAlgo "Smart Money
Concepts" Pine script's swing-structure logic.

This is a DIFFERENT definition from smc_backtest.structure_bias_series(),
which compares the last two confirmed swings (HH+HL = bullish, LH+LL =
bearish). That version is slow: it needs two swings on each side to
agree, so bias lags the actual turn badly. On the daily chart it flipped
too late to be useful (see daily_fvg_structure_filter.py).

LuxAlgo's definition instead flips bias the moment CLOSE crosses the last
still-unbroken pivot:

    crossover(close, lastPivotHigh)  and not crossed
        -> BOS   if already bullish   (trend continuing)
        -> CHoCH if currently bearish (trend changing)
        -> bias becomes BULLISH
    crossunder(close, lastPivotLow)  and not crossed
        -> BOS   if already bearish
        -> CHoCH if currently bullish
        -> bias becomes BEARISH

Pivot detection follows LuxAlgo's leg() exactly: with lookback `size`,
the bar `size` back is a pivot HIGH when its high exceeds the highest
high of the `size` bars since (a "bearish leg" starts), and a pivot LOW
when its low undercuts the lowest low since. That means a pivot at index
j is only detected at index j+size -- non-repainting by construction, and
the cross that breaks it can only happen later still.

NON-REPAINTING: bias[i] uses only bars up to and including i.
"""

import numpy as np
import pandas as pd

BULLISH = 1
BEARISH = -1
NEUTRAL = 0


def compute_structure(df, size=5):
    """LuxAlgo swing-structure bias.

    Returns a DataFrame aligned to df with columns:
      bias   -- +1 bullish / -1 bearish / 0 not yet established
      event  -- '', 'BOS' or 'CHoCH' on the bar the break confirmed
      dirn   -- +1 / -1 direction of that event
    """
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    n = len(df)

    bias = np.zeros(n, dtype=int)
    event = np.array([""] * n, dtype=object)
    dirn = np.zeros(n, dtype=int)

    # leg state, mirroring LuxAlgo's leg(size)
    leg = 0
    pivot_high = np.nan   # last pivot high level
    pivot_low = np.nan
    ph_crossed = True     # nothing to break until a pivot exists
    pl_crossed = True
    cur_bias = NEUTRAL

    for i in range(n):
        if i >= size:
            j = i - size                     # the candidate pivot bar
            window_h = high[j + 1:i + 1]     # the `size` bars since it
            window_l = low[j + 1:i + 1]
            new_leg_high = window_h.size > 0 and high[j] > window_h.max()
            new_leg_low = window_l.size > 0 and low[j] < window_l.min()

            prev_leg = leg
            if new_leg_high:
                leg = 0                      # bearish leg
            elif new_leg_low:
                leg = 1                      # bullish leg

            if leg != prev_leg:
                if leg == 0:                 # start of bearish leg -> pivot HIGH at j
                    pivot_high = high[j]
                    ph_crossed = False
                else:                        # start of bullish leg -> pivot LOW at j
                    pivot_low = low[j]
                    pl_crossed = False

        # break detection: close crossing the last unbroken pivot
        if not np.isnan(pivot_high) and not ph_crossed and close[i] > pivot_high:
            event[i] = "CHoCH" if cur_bias == BEARISH else "BOS"
            dirn[i] = BULLISH
            ph_crossed = True
            cur_bias = BULLISH
        elif not np.isnan(pivot_low) and not pl_crossed and close[i] < pivot_low:
            event[i] = "CHoCH" if cur_bias == BULLISH else "BOS"
            dirn[i] = BEARISH
            pl_crossed = True
            cur_bias = BEARISH

        bias[i] = cur_bias

    return pd.DataFrame({"time": df["time"].values, "bias": bias,
                         "event": event, "dirn": dirn})


def bias_at(structure, times, when):
    """Bias as of strictly BEFORE `when` (a np.datetime64 / Timestamp).

    Used to read an HTF/LTF structure bias at the moment a daily trade
    fires, without letting the bar containing the entry leak in.
    """
    idx = np.searchsorted(times, when, side="left") - 1
    if idx < 0:
        return NEUTRAL
    return int(structure["bias"].values[idx])
