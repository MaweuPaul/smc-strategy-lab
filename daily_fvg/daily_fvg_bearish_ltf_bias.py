"""
Test: does the bearish FVG retracement-sell work when filtered by H4/H1
BEARISH structure bias -- the exact mirror of the filter that fixed the
long side (daily_fvg_ltf_bias.py)?

Context: the plain bearish mirror (daily_fvg_bearish.py) lost on every
symbol. Filtering it by the SLOW daily 200D SMA didn't rescue it either
(daily_fvg_winrate_levers.py / daily_fvg_structure_filter.py). But both
of those failures predate discovering that FAST H4/H1 BOS-CHoCH bias
(smc_structure.py) is what actually works for the long side. This asks
the un-tested question: does the same fast filter rescue the short side,
requiring BEARISH H4/H1 bias instead of bullish?

Same data constraint as daily_fvg_ltf_bias.py: H4/H1 only reliable from
~2021-07 onward on this terminal, so baseline is restricted to the same
window for a fair comparison.
"""


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # repo root, for shared modules (smc_backtest, etc.)

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from smc_structure import compute_structure, bias_at, BEARISH
from daily_fvg_bearish import find_bearish_entries, simulate_short
from daily_fvg_newday import fetch, START, END
from p404_sweep_reversal import compute_atr14
import daily_fvg_newday as fvg_mod
from backend_api import reliable_intraday_start

SYMBOLS = ["XAUUSD", "XAGUSD", "USTEC", "US30", "US500", "USDJPY", "GBPUSD", "EURUSD"]
METALS = ["XAUUSD", "XAGUSD"]


def rr_for(s):
    return 3.0 if s in METALS else 2.0


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
        print(f"{label:>16}    no trades")
        return
    print(f"{label:>16} {s['n']:4d} {s['win']:6.1f} {s['avgR']:+7.3f} {s['pf']:5.2f} "
          f"{s['dd']:7.1f} {s['streak']:7d} {s['final']:10,.0f}")


def main():
    for symbol in SYMBOLS:
        d1 = fetch(symbol, mt5.TIMEFRAME_D1, START, END)
        m15 = fetch(symbol, mt5.TIMEFRAME_M15, START, END)
        h4 = fetch(symbol, mt5.TIMEFRAME_H4, START, END)

        atr = compute_atr14(d1).values
        fvg_mod.ATR_BUFFER_MULT = buf_for(symbol)
        fvg_mod.TARGET_RR = rr_for(symbol)
        fvg_mod.SIZING_MODE = "risk_pct"
        fvg_mod.RISK_PCT = 0.015

        entries = find_bearish_entries(d1, m15)
        for _, g in entries:
            g["_atr"] = atr[g["formed_at"]] if not np.isnan(atr[g["formed_at"]]) else 0.0

        reliable_from = reliable_intraday_start(h4)
        if reliable_from is None:
            print(f"{symbol}: no usable H4 history, skipping")
            continue
        h4c = h4[h4["time"] >= reliable_from].reset_index(drop=True)
        m15_time = m15["time"].values
        cutoff = np.datetime64(pd.Timestamp(reliable_from).tz_localize(None))
        clean = [(i, g) for i, g in entries if m15_time[i] >= cutoff]

        print(f"\n=== {symbol} (RR={rr_for(symbol)}, buf={buf_for(symbol)}) "
              f"-- {len(clean)} entries since {pd.Timestamp(reliable_from).date()} ===")
        print(f"{'bias':>16} {'n':>4} {'win%':>6} {'avgR':>7} {'PF':>5} "
              f"{'DD%':>7} {'lossRun':>7} {'final':>10}")

        tr, curve = simulate_short(m15, clean)
        show("none (baseline)", tr, curve)

        ht = h4c["time"].values
        for size in (5, 10):
            st = compute_structure(h4c, size=size)
            kept = [(i, g) for i, g in clean if bias_at(st, ht, m15_time[i]) == BEARISH]
            tr, curve = simulate_short(m15, kept)
            show(f"H4 bearish/{size}", tr, curve)


if __name__ == "__main__":
    main()
