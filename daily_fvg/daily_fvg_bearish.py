"""
Bearish mirror of daily_fvg_newday.py's retracement-buy rule: wait for
price to retrace back INTO a bearish daily FVG, sell at the next day's
open, stop above the gap, fixed-RR target below.

The bullish-only version was long-only by original spec, not because
bearish FVGs don't happen -- they're just as common (a daily FVG forms
whichever direction that 3-candle imbalance points), and D1 downtrends
produce bearish FVGs the same way uptrends produce bullish ones. This
mirrors the exact same rule with signs flipped, to test whether shorting
the down-trend equivalent has a comparable edge.
"""


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # repo root, for shared modules (smc_backtest, etc.)

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from smc_backtest import find_fvgs
from daily_fvg_newday import fetch, SYMBOLS, START, END
import daily_fvg_newday as fvg_mod


def find_bearish_entries(d1, m15):
    """Mirror of find_entries(): same incremental active/inverted scan,
    just for bearish gaps -- entered when price trades back UP into the
    gap, inverted when D1 close breaks back above the gap's top."""
    fvgs = find_fvgs(d1)
    only_bearish = [g for g in fvgs if g["type"] == "bearish"]
    d1_close = d1["close"].values
    d1_low, d1_high, d1_date = d1["low"].values, d1["high"].values, d1["time"].dt.date.values
    m15_date = m15["time"].dt.date.values
    n = len(d1)

    first_m15_idx_for_date = {}
    for i, dt in enumerate(m15_date):
        if dt not in first_m15_idx_for_date:
            first_m15_idx_for_date[dt] = i

    active = []
    fvg_ptr = 0
    entries = []
    for day_idx in range(n):
        while fvg_ptr < len(only_bearish) and only_bearish[fvg_ptr]["formed_at"] == day_idx:
            active.append(only_bearish[fvg_ptr])
            fvg_ptr += 1

        still_active = []
        for gap in active:
            if day_idx <= gap["formed_at"]:
                still_active.append(gap)
                continue
            if d1_close[day_idx] > gap["top"]:
                continue  # inverted -- fully closed back through, drop it
            entered = d1_high[day_idx] >= gap["bottom"] and d1_low[day_idx] <= gap["top"]
            if entered:
                gap["_entered_day_idx"] = day_idx
                next_date = d1_date[day_idx + 1] if day_idx + 1 < n else None
                if next_date is not None and next_date in first_m15_idx_for_date:
                    entries.append((first_m15_idx_for_date[next_date], gap))
                continue
            still_active.append(gap)
        active = still_active
    return sorted(entries, key=lambda e: e[0])


def simulate_short(m15, entries):
    """Mirror of fvg_mod.simulate(): sell at next-day open, stop = gap
    top + ATR buffer, target = fixed RR below entry."""
    o, h, l, c = m15["open"].values, m15["high"].values, m15["low"].values, m15["close"].values
    t = m15["time"].values
    m = len(m15)

    equity = fvg_mod.START_EQUITY
    equity_curve = [equity]
    trades = []
    i = 0
    for entry_idx, gap in entries:
        if entry_idx < i:
            continue
        buffer = fvg_mod.ATR_BUFFER_MULT * gap.get("_atr", 0.0)
        stop = gap["top"] + buffer
        entry_price = o[entry_idx]
        r = stop - entry_price
        if r <= 0:
            continue
        target = entry_price - fvg_mod.TARGET_RR * r

        if fvg_mod.SIZING_MODE == "fixed_units":
            qty = fvg_mod.FIXED_UNITS
        else:
            qty = min((equity * fvg_mod.RISK_PCT) / r, (equity * fvg_mod.MAX_LEVERAGE) / entry_price)
        risk_amount = qty * r
        entry_commission = qty * entry_price * fvg_mod.COMMISSION_RATE
        equity -= entry_commission

        exit_price = exit_idx = exit_reason = None
        for k in range(entry_idx, m):
            if o[k] >= stop and k > entry_idx:
                exit_price, exit_idx, exit_reason = o[k], k, "gap_stop"
                break
            if h[k] >= stop:
                exit_price, exit_idx, exit_reason = stop, k, "stop"
                break
            if l[k] <= target:
                exit_price, exit_idx, exit_reason = target, k, "target"
                break
        if exit_price is None:
            exit_price, exit_idx, exit_reason = c[m - 1], m - 1, "data_end"

        pnl = qty * (entry_price - exit_price) - abs(qty * exit_price) * fvg_mod.COMMISSION_RATE
        equity += pnl
        equity_curve.append(equity)
        trades.append({
            "entry_idx": entry_idx, "entry_time": t[entry_idx], "entry_price": entry_price,
            "stop": stop, "target": target, "exit_time": t[exit_idx], "exit_price": exit_price,
            "exit_reason": exit_reason, "pnl": pnl,
            "r_multiple": pnl / risk_amount if risk_amount else np.nan,
        })
        i = exit_idx + 1
    return pd.DataFrame(trades), equity_curve


def summarize(trades, curve, symbol):
    print(f"\n=== {symbol} (bearish retracement-sell) ===")
    n = len(trades)
    if n == 0:
        print("No trades.")
        return
    wins = (trades["pnl"] > 0).sum()
    profit = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    loss = -trades.loc[trades["pnl"] < 0, "pnl"].sum()
    vals = np.asarray(curve)
    peak = np.maximum.accumulate(vals)
    print(f"  n={n} win%={100*wins/n:.1f} avgR={trades['r_multiple'].mean():+.3f} "
          f"PF={(profit/loss if loss else float('nan')):.2f} "
          f"DD={(((vals-peak)/peak).min()*100):.1f}% final_eq=${curve[-1]:,.0f}")


def main():
    from p404_sweep_reversal import compute_atr14
    METALS = ["XAUUSD", "XAGUSD"]
    def rr_for(s): return 3.0 if s in METALS else 2.0
    def buf_for(s):
        if s in METALS: return 1.0
        if s == "USDJPY": return 0.75
        return 0.25

    for symbol in SYMBOLS:
        print(f"\nFetching {symbol} D1 + M15 {START.date()} -> {END.date()} ...")
        d1 = fetch(symbol, mt5.TIMEFRAME_D1, START, END)
        m15 = fetch(symbol, mt5.TIMEFRAME_M15, START, END)
        fvg_mod.TARGET_RR = rr_for(symbol)
        fvg_mod.ATR_BUFFER_MULT = buf_for(symbol)
        atr = compute_atr14(d1).values
        entries = find_bearish_entries(d1, m15)
        for _, gap in entries:
            gap["_atr"] = atr[gap["formed_at"]] if not np.isnan(atr[gap["formed_at"]]) else 0.0
        print(f"  bearish retracement candidates: {len(entries)} (RR={rr_for(symbol)}, buf={buf_for(symbol)})")
        trades, curve = simulate_short(m15, entries)
        summarize(trades, curve, symbol)


if __name__ == "__main__":
    main()
