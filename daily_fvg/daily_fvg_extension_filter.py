"""
RESULT: FAILED, and for a structural reason worth remembering.

Losers are LESS extended than winners, not more (Gold: mean extension
+1.44 for losers vs +1.70 for winners). The relationship runs the wrong
way, so there is nothing to filter on.

WHY: the entry trigger is a retracement INTO the gap -- price has to fall
to fire the trade at all. That pullback unwinds most of the extension
before entry. You cannot filter on "price is stretched" when the entry
mechanism only fires after price has already come back down.

Gold's Jan-Feb 2026 loss cluster shows it concretely:
    2026-01-27  ext +6.97  -> +1.98R  WIN  (most extended trade of the year)
    2026-01-30  ext +6.45  -> -1.01R  loss
    2026-02-01  ext +1.88  -> -1.01R  loss
    2026-02-03  ext +1.02  -> -1.01R  loss
    2026-03-24  ext -3.45  -> -1.00R  loss  (BELOW its mean)
    2026-07-07  ext -2.02  -> -1.02R  loss  (BELOW its mean)
Any threshold tight enough to block the 01-30 loss also discards the
01-27 winner, and three of the five losses were not extended at all.

Gold's max drawdown is -10.5% at EVERY threshold tested -- identical to
baseline -- i.e. the filter never once removed a trade responsible for
the drawdown. Every threshold made Gold and Silver worse outright. Only
USDJPY improved consistently (1 of 6 symbols, i.e. noise); US30 and US500
improved at a single threshold each, which is selection, not signal.

Over-extension filter for the daily FVG retracement longs.

Motivation: trend-confirmation filters (daily HH/HL, H4 BOS/CHoCH) cannot
catch the first leg down off a major top, because confirmation by
definition arrives after the turn. On Gold's Jan-Feb 2026 cluster, H4
structure still read bullish through all three consecutive losses.

This tests a different mechanism entirely -- not trend DIRECTION but how
STRETCHED price is from its own mean. The premise: a dip bought while
price is still far above its average is a dip inside a blow-off, and the
"gap holds" thesis is much weaker there than in a normal pullback.

Extension is measured on the TRIGGER day's close (known at that day's
close; entry is still the next day's open, so nothing leaks) and
normalised by ATR so it is comparable across symbols and price levels:

    extension = (trigger_close - SMA_n) / ATR14

Step 1 asks whether extension predicts outcome at all, by bucketing
trades into quintiles. Only if high-extension buckets are visibly worse
is a threshold filter worth fitting -- otherwise it is noise.
"""


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # repo root, for shared modules (smc_backtest, etc.)

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from daily_fvg_newday import fetch, find_entries, simulate, START, END
from p404_sweep_reversal import compute_atr14
import daily_fvg_newday as fvg_mod

SYMBOLS = ["XAUUSD", "XAGUSD", "USTEC", "US30", "US500", "USDJPY"]
METALS = ["XAUUSD", "XAGUSD"]


def rr_for(s):
    return 3.0 if s == "XAGUSD" else 2.0


def buf_for(s):
    if s in METALS:
        return 1.0
    if s == "USDJPY":
        return 0.75
    return 0.25


def extension_series(d1, sma_len=50):
    """(close - SMA) / ATR14, per D1 bar. Backward-looking only."""
    sma = d1["close"].rolling(sma_len).mean().values
    atr = compute_atr14(d1).values
    close = d1["close"].values
    with np.errstate(invalid="ignore", divide="ignore"):
        return (close - sma) / atr


def prepare(symbol, sma_len=50):
    d1 = fetch(symbol, mt5.TIMEFRAME_D1, START, END)
    m15 = fetch(symbol, mt5.TIMEFRAME_M15, START, END)
    atr = compute_atr14(d1).values
    fvg_mod.ATR_BUFFER_MULT = buf_for(symbol)
    fvg_mod.TARGET_RR = rr_for(symbol)
    fvg_mod.SIZING_MODE = "risk_pct"
    fvg_mod.RISK_PCT = 0.015
    entries = find_entries(d1, m15)
    for _, g in entries:
        g["_atr"] = atr[g["formed_at"]] if not np.isnan(atr[g["formed_at"]]) else 0.0
    ext = extension_series(d1, sma_len)
    for _, g in entries:
        day = g.get("_entered_day_idx")
        g["_ext"] = ext[day] if day is not None else np.nan
    return d1, m15, entries


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


def step1_does_extension_predict(sma_len=50):
    print(f"STEP 1 -- does extension predict outcome?  (SMA{sma_len}, quintiles)\n")
    for symbol in SYMBOLS:
        d1, m15, entries = prepare(symbol, sma_len)
        trades, _ = simulate(m15, entries)
        by_idx = dict(entries)
        rows = []
        for _, t in trades.iterrows():
            g = by_idx.get(t["entry_idx"], {})
            rows.append({"ext": g.get("_ext", np.nan), "R": t["r_multiple"],
                         "win": t["pnl"] > 0})
        df = pd.DataFrame(rows).dropna(subset=["ext"])
        if len(df) < 20:
            print(f"{symbol}: too few trades\n")
            continue
        df["bucket"] = pd.qcut(df["ext"], 5, labels=False, duplicates="drop")
        g = df.groupby("bucket").agg(n=("R", "size"), lo=("ext", "min"),
                                     hi=("ext", "max"),
                                     win=("win", lambda s: 100 * s.mean()),
                                     avgR=("R", "mean"))
        print(f"=== {symbol} ===")
        print(f"{'bucket':>7} {'ext range':>16} {'n':>4} {'win%':>6} {'avgR':>7}")
        for b, r in g.iterrows():
            print(f"{int(b):7d} {r['lo']:7.2f}..{r['hi']:6.2f} {int(r['n']):4d} "
                  f"{r['win']:6.1f} {r['avgR']:+7.3f}")
        print()


def step2_threshold_sweep(sma_len=50, thresholds=(1.5, 2.0, 2.5, 3.0, 4.0)):
    print(f"\nSTEP 2 -- skip entries with extension above threshold (SMA{sma_len})\n")
    for symbol in SYMBOLS:
        d1, m15, entries = prepare(symbol, sma_len)
        print(f"=== {symbol} ===")
        print(f"{'filter':>14} {'n':>4} {'win%':>6} {'avgR':>7} {'PF':>5} "
              f"{'DD%':>7} {'lossRun':>7} {'final':>10}")
        tr, curve = simulate(m15, entries)
        s = stats(tr, curve)
        print(f"{'none':>14} {s['n']:4d} {s['win']:6.1f} {s['avgR']:+7.3f} {s['pf']:5.2f} "
              f"{s['dd']:7.1f} {s['streak']:7d} {s['final']:10,.0f}")
        for th in thresholds:
            kept = [(i, g) for i, g in entries
                    if not np.isnan(g.get("_ext", np.nan)) and g["_ext"] <= th]
            tr, curve = simulate(m15, kept)
            s = stats(tr, curve)
            if s is None:
                print(f"{f'ext <= {th}':>14}    no trades")
                continue
            print(f"{f'ext <= {th}':>14} {s['n']:4d} {s['win']:6.1f} {s['avgR']:+7.3f} "
                  f"{s['pf']:5.2f} {s['dd']:7.1f} {s['streak']:7d} {s['final']:10,.0f}")
        print()


if __name__ == "__main__":
    step1_does_extension_predict(50)
    step2_threshold_sweep(50)
