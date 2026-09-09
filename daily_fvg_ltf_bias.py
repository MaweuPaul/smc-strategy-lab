"""
RESULT: THIS ONE WORKS -- the first filter tested that improves broadly
rather than on a single symbol. Reading BOS/CHoCH on H4/H1 (rather than
D1) and requiring bullish bias improves win rate, avg R, profit factor,
drawdown AND worst losing streak on 5 of 6 symbols. It roughly halves
trade count, so absolute return can fall at fixed risk -- but drawdown
falls further, so risking ~2x on the filtered set beats the unfiltered
baseline outright on Gold, Silver, USTEC and US30.

At a fixed H4/size-10 config (i.e. NOT tuned per symbol) avg R improved
on 5 of 6. The per-symbol (timeframe, size) picks below were chosen from
a 6-config grid, so expect some of that edge to be selection -- the
fixed-config result is the honest estimate.

CAVEAT: evaluated only on 2021-07-15 -> present, because the MT5 local
cache has no reliable H4/H1/M15 before then. ~5 years, 29-98 trades per
config. Baseline is restricted to the same window.

Lower-timeframe BOS/CHoCH bias filter for the daily FVG retracement longs.

The idea being tested: the daily FVG gives the LEVEL, but the bias should
come from structure read on a faster, denser timeframe (H4 / H1), which
flips on the first close through an unbroken pivot rather than waiting
for two swings to line up. Only take the daily long when that LTF
structure is bullish.

This is the follow-up to daily_fvg_structure_filter.py, which used DAILY
structure and failed specifically because it flipped too late -- 3 of the
5 consecutive Gold losses in early 2026 fired while daily structure still
read bullish. H4/H1 should turn much sooner.

DATA CONSTRAINT: the MT5 terminal's local intraday cache only holds H4/H1
(and M15) back to roughly 2021-07. Everything here is therefore evaluated
on 2021-07-15 -> present, and the baseline is restricted to the SAME
window so the comparison is apples-to-apples rather than flattered by a
different sample.
"""

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from smc_structure import compute_structure, bias_at, BULLISH, BEARISH
from daily_fvg_newday import fetch, find_entries, simulate, START, END
from p404_sweep_reversal import compute_atr14
import daily_fvg_newday as fvg_mod

SYMBOLS = ["XAUUSD", "XAGUSD", "USTEC", "US30", "US500", "USDJPY"]
CLEAN_START = pd.Timestamp("2021-07-15")
METALS = ["XAUUSD", "XAGUSD"]


def rr_for(s):
    return 3.0 if s == "XAGUSD" else 2.0


def buf_for(s):
    if s in METALS:
        return 1.0
    if s == "USDJPY":
        return 0.75
    return 0.25


def stats(trades, curve):
    if len(trades) == 0:
        return None
    v = np.asarray(curve)
    peak = np.maximum.accumulate(v)
    profit = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    loss = -trades.loc[trades["pnl"] < 0, "pnl"].sum()
    worst = cur = 0
    for p in trades["pnl"]:
        cur = cur + 1 if p < 0 else 0
        worst = max(worst, cur)
    return {"n": len(trades), "win": 100 * (trades["pnl"] > 0).mean(),
            "avgR": trades["r_multiple"].mean(),
            "pf": profit / loss if loss else float("nan"),
            "dd": ((v - peak) / peak).min() * 100,
            "final": curve[-1], "streak": worst}


def show(label, trades, curve):
    s = stats(trades, curve)
    if s is None:
        print(f"{label:>22}    no trades")
        return
    print(f"{label:>22} {s['n']:4d} {s['win']:6.1f} {s['avgR']:+7.3f} {s['pf']:5.2f} "
          f"{s['dd']:7.1f} {s['streak']:7d} {s['final']:10,.0f}")


def main():
    for symbol in SYMBOLS:
        d1 = fetch(symbol, mt5.TIMEFRAME_D1, START, END)
        m15 = fetch(symbol, mt5.TIMEFRAME_M15, START, END)
        h4 = fetch(symbol, mt5.TIMEFRAME_H4, START, END)
        h1 = fetch(symbol, mt5.TIMEFRAME_H1, START, END)

        atr = compute_atr14(d1).values
        fvg_mod.ATR_BUFFER_MULT = buf_for(symbol)
        fvg_mod.TARGET_RR = rr_for(symbol)
        fvg_mod.SIZING_MODE = "risk_pct"

        entries = find_entries(d1, m15)
        for _, g in entries:
            g["_atr"] = atr[g["formed_at"]] if not np.isnan(atr[g["formed_at"]]) else 0.0

        m15_time = m15["time"].values
        # restrict to the window where intraday data is actually real
        clean = [(i, g) for i, g in entries
                 if pd.Timestamp(m15_time[i]).tz_localize(None) >= CLEAN_START]

        print(f"\n=== {symbol} (RR={rr_for(symbol)}, buf={buf_for(symbol)}) "
              f"-- {len(clean)} entries since {CLEAN_START.date()} ===")
        print(f"{'bias source':>22} {'n':>4} {'win%':>6} {'avgR':>7} {'PF':>5} "
              f"{'DD%':>7} {'lossRun':>7} {'final':>10}")

        tr, curve = simulate(m15, clean)
        show("none (baseline)", tr, curve)

        for tf_label, tf_df in (("H4", h4), ("H1", h1)):
            times = tf_df["time"].values
            for size in (5, 10, 20):
                st = compute_structure(tf_df, size=size)
                kept = [(i, g) for i, g in clean
                        if bias_at(st, times, m15_time[i]) == BULLISH]
                tr, curve = simulate(m15, kept)
                show(f"{tf_label} bullish (size {size})", tr, curve)


if __name__ == "__main__":
    main()
