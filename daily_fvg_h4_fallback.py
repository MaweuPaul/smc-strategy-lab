"""
Extends daily_fvg_newday.py with two additions, per request:

  1. H4 fallback: on any day with NO currently-active (formed, not yet
     entered or inverted) daily bullish FVG, also watch H4 bullish FVGs
     that day. An H4 entry fires at the OPEN of the NEXT H4 candle
     (analogous to the daily version's "next day open"), still executed/
     monitored on M15 for exit precision.
  2. Discount filter: before EITHER a daily or H4 entry is taken, price
     must be trading in "discount" -- below the 50% equilibrium of the
     most recent CONFIRMED D1 swing high/low (same confirmed N-bar
     fractal method used in discount_breakout_backtest.py, non-
     repainting). Long-only strategy, so only "discount" applies here;
     "premium" would be the symmetric gate for a short/bearish variant,
     which isn't built since the base spec is buy-only.

Nothing else changes from daily_fvg_newday.py -- same stop (gap bottom -
0.25x ATR), same fixed 2:1 target, same one-trade-at-a-time M15
execution model.
"""

import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime

from smc_backtest import find_fvgs
from p404_sweep_reversal import _server_utc_offset_hours, compute_atr14
from discount_breakout_backtest import compute_confirmed_swings
from daily_fvg_newday import fetch, simulate, summarize, SYMBOLS, START, END, ATR_BUFFER_MULT

DISCOUNT_SWING_N = 5


def compute_discount_by_date(d1):
    """True where D1 close is below the 50% equilibrium of the most
    recent confirmed swing high/low -- looked up per calendar date."""
    sh, sl = compute_confirmed_swings(d1, n=DISCOUNT_SWING_N)
    eq = (sh + sl) / 2
    discount = d1["close"].values < eq
    return dict(zip(d1["time"].dt.date.values, discount))


def find_daily_entries_and_availability(d1):
    """Same as daily_fvg_newday.find_entries, but also returns, per D1
    calendar date, whether a daily bullish FVG was actively awaiting
    retracement that day (used to decide when the H4 fallback applies)."""
    fvgs = find_fvgs(d1)
    only_bullish = [g for g in fvgs if g["type"] == "bullish"]
    d1_close, d1_low, d1_high = d1["close"].values, d1["low"].values, d1["high"].values
    d1_date = d1["time"].dt.date.values
    n = len(d1)

    active, fvg_ptr, entries = [], 0, []
    has_active_daily = {}
    for day_idx in range(n):
        while fvg_ptr < len(only_bullish) and only_bullish[fvg_ptr]["formed_at"] == day_idx:
            active.append(only_bullish[fvg_ptr])
            fvg_ptr += 1
        has_active_daily[d1_date[day_idx]] = len(active) > 0

        still_active = []
        for gap in active:
            if day_idx <= gap["formed_at"]:
                still_active.append(gap)
                continue
            if d1_close[day_idx] < gap["bottom"]:
                continue
            entered = d1_low[day_idx] <= gap["top"] and d1_high[day_idx] >= gap["bottom"]
            if entered:
                entries.append((day_idx, gap, "daily"))
                continue
            still_active.append(gap)
        active = still_active
    return entries, has_active_daily


def find_h4_entries(h4, has_active_daily):
    """H4 bullish FVG entries, only on H4 bars whose calendar date has NO
    active daily FVG. Fires at the open of the NEXT H4 bar."""
    fvgs = find_fvgs(h4)
    only_bullish = [g for g in fvgs if g["type"] == "bullish"]
    h4_close, h4_low, h4_high = h4["close"].values, h4["low"].values, h4["high"].values
    h4_date = h4["time"].dt.date.values
    n = len(h4)

    active, fvg_ptr, entries = [], 0, []
    for bar_idx in range(n):
        while fvg_ptr < len(only_bullish) and only_bullish[fvg_ptr]["formed_at"] == bar_idx:
            active.append(only_bullish[fvg_ptr])
            fvg_ptr += 1

        if has_active_daily.get(h4_date[bar_idx], False):
            # a daily FVG is already in play today -- daily takes priority
            continue

        still_active = []
        for gap in active:
            if bar_idx <= gap["formed_at"]:
                still_active.append(gap)
                continue
            if h4_close[bar_idx] < gap["bottom"]:
                continue
            entered = h4_low[bar_idx] <= gap["top"] and h4_high[bar_idx] >= gap["bottom"]
            if entered:
                if bar_idx + 1 < n:
                    entries.append((bar_idx + 1, gap, "h4"))
                continue
            still_active.append(gap)
        active = still_active
    return entries


def build_m15_entries(symbol, d1, h4, m15):
    daily_entries, has_active_daily = find_daily_entries_and_availability(d1)
    h4_entries = find_h4_entries(h4, has_active_daily)
    discount_by_date = compute_discount_by_date(d1)
    atr_d1 = compute_atr14(d1).values
    atr_h4 = compute_atr14(h4).values

    d1_date = d1["time"].dt.date.values
    m15_date = m15["time"].dt.date.values
    m15_time = m15["time"].values
    first_m15_idx_for_date = {}
    for i, dt in enumerate(m15_date):
        if dt not in first_m15_idx_for_date:
            first_m15_idx_for_date[dt] = i

    out = []
    n_d1 = len(d1)
    for day_idx, gap, src in daily_entries:
        day_date = d1_date[day_idx]
        if not discount_by_date.get(day_date, False):
            continue
        next_date = d1_date[day_idx + 1] if day_idx + 1 < n_d1 else None
        if next_date is None or next_date not in first_m15_idx_for_date:
            continue
        gap["_atr"] = atr_d1[gap["formed_at"]] if not np.isnan(atr_d1[gap["formed_at"]]) else 0.0
        out.append((first_m15_idx_for_date[next_date], gap, src))

    for bar_idx, gap, src in h4_entries:
        bar_date = h4["time"].dt.date.iloc[bar_idx]
        if not discount_by_date.get(bar_date, False):
            continue
        target_time = h4["time"].iloc[bar_idx]
        m15_idx = np.searchsorted(m15_time, np.datetime64(target_time))
        if m15_idx >= len(m15):
            continue
        gap["_atr"] = atr_h4[gap["formed_at"]] if not np.isnan(atr_h4[gap["formed_at"]]) else 0.0
        out.append((m15_idx, gap, src))

    out.sort(key=lambda e: e[0])
    return out


def main():
    for symbol in SYMBOLS:
        print(f"\nFetching {symbol} D1 + H4 + M15 {START.date()} -> {END.date()} ...")
        d1 = fetch(symbol, mt5.TIMEFRAME_D1, START, END)
        h4 = fetch(symbol, mt5.TIMEFRAME_H4, START, END)
        m15 = fetch(symbol, mt5.TIMEFRAME_M15, START, END)
        print(f"  {len(d1)} D1 bars, {len(h4)} H4 bars, {len(m15)} M15 bars")

        entries = build_m15_entries(symbol, d1, h4, m15)
        n_daily = sum(1 for _, _, src in entries if src == "daily")
        n_h4 = sum(1 for _, _, src in entries if src == "h4")
        print(f"  discount-filtered entries: daily={n_daily} h4_fallback={n_h4}")

        sim_entries = [(idx, gap) for idx, gap, _ in entries]
        trades, curve = simulate(m15, sim_entries)
        summarize(trades, curve, symbol)


if __name__ == "__main__":
    main()
