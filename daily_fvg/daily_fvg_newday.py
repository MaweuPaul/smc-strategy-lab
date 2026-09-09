"""
Daily FVG "wait for retracement, buy next day open" backtest.

Rule, as specified:
  1. Detect Fair Value Gaps (3-candle imbalance) on the DAILY (D1) chart.
  2. Wait for price to trade back INTO a bullish daily FVG (that day's
     range overlaps the gap's [bottom, top] zone), on or after the day
     the gap actually confirmed (formed_at = D1 bar i+1 -- known only
     once that bar has closed, non-repainting).
  3. If the FVG is bullish, buy at the OPEN of the NEXT calendar day,
     executed on the M15 chart (first M15 candle of that new day gives
     the fill price).
  4. Long only (per the spec's own wording), one trade at a time, each
     FVG can trigger at most once.

ASSUMPTIONS (not specified by the user -- flagged, not tuned against
results):
  - Stop-loss: the FVG's own bottom edge minus 0.25x D1 ATR14 -- if price
    closes back below the gap, the "still-valid-imbalance" thesis is
    wrong. Same structural-stop convention used elsewhere this session.
  - Target: fixed 2:1 reward:risk, single exit, no partials (the spec
    didn't describe management, so kept as simple as the entry rule).
  - A gap that gets fully invalidated (D1 close below its bottom) before
    being entered is dropped (same inversion condition as
    smc_backtest.update_fvg_status(), reimplemented inline here for an
    O(n) incremental scan instead of that function's O(n) full rescan
    per call).
  - "New day" = UTC calendar day, matching MT5's own D1 bar timestamps
    directly (avoids introducing a second timezone convention).
  - Entry candle (the new day's first M15 bar) is still checked for its
    own intrabar stop/target after the open, with gap-aware fills and a
    stop-first tie-break -- same execution model as the other scripts
    built this session.
"""


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # repo root, for shared modules (smc_backtest, etc.)

import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, timezone

from smc_backtest import find_fvgs
from p404_sweep_reversal import _server_utc_offset_hours, compute_atr14

SYMBOLS = ["USTEC", "EURUSD", "XAUUSD", "XAGUSD", "GBPUSD", "USDJPY"]
START = datetime(2000, 1, 1)
END = datetime(2026, 9, 8)

ATR_BUFFER_MULT = 0.25
TARGET_RR = 2.0
RISK_PCT = 0.015
MAX_LEVERAGE = 5.0
COMMISSION_RATE = 0.0005
START_EQUITY = 100_000.0

# Position sizing mode: "risk_pct" (default) sizes each trade so its stop
# risks a fixed % of current equity -- position size swings with the
# stop distance (wider ATR stop -> smaller size). "fixed_units" instead
# trades the same quantity every time regardless of stop distance -- the
# manual-trading way of "I always trade N units/lots", useful when a
# fast-moving instrument (e.g. Gold) makes the stop distance -- and so
# the risk-based size -- swing a lot trade to trade.
SIZING_MODE = "risk_pct"
FIXED_UNITS = 10.0


def fetch(symbol, timeframe, start, end):
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    if not mt5.symbol_select(symbol, True):
        mt5.shutdown()
        raise RuntimeError(f"Could not select symbol {symbol}")
    offset_h = _server_utc_offset_hours(symbol)
    rates = mt5.copy_rates_range(symbol, timeframe, start, end)
    mt5.shutdown()
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No data for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(rates)[["time", "open", "high", "low", "close"]]
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=offset_h)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def find_entries(d1, m15):
    """Returns list of (m15_entry_idx, fvg) for each bullish FVG the first
    time it's entered (touched) on or after its own formation day, fired
    at the OPEN of the next calendar day's first M15 bar.

    Inversion (full close-through invalidation) is checked incrementally,
    one new day at a time per active gap -- NOT via repeated calls to
    smc_backtest.update_fvg_status(), which rescans from a gap's whole
    formation history on every call and would be O(n^3) here across ~2500
    D1 bars and however many gaps are active at once."""
    fvgs = find_fvgs(d1)
    only_bullish = [g for g in fvgs if g["type"] == "bullish"]
    d1_close = d1["close"].values
    d1_low, d1_high, d1_date = d1["low"].values, d1["high"].values, d1["time"].dt.date.values
    m15_date = m15["time"].dt.date.values
    n = len(d1)

    first_m15_idx_for_date = {}
    for i, dt in enumerate(m15_date):
        if dt not in first_m15_idx_for_date:
            first_m15_idx_for_date[dt] = i

    active = []  # gaps already formed, not yet inverted/used, waiting for entry
    fvg_ptr = 0
    entries = []
    for day_idx in range(n):
        while fvg_ptr < len(only_bullish) and only_bullish[fvg_ptr]["formed_at"] == day_idx:
            active.append(only_bullish[fvg_ptr])
            fvg_ptr += 1

        still_active = []
        for gap in active:
            if day_idx <= gap["formed_at"]:
                still_active.append(gap)
                continue
            if d1_close[day_idx] < gap["bottom"]:
                continue  # inverted -- fully closed back through, drop it
            entered = d1_low[day_idx] <= gap["top"] and d1_high[day_idx] >= gap["bottom"]
            if entered:
                gap["_entered_day_idx"] = day_idx  # for API "why" narratives; harmless extra key otherwise
                next_date = d1_date[day_idx + 1] if day_idx + 1 < n else None
                if next_date is not None and next_date in first_m15_idx_for_date:
                    entries.append((first_m15_idx_for_date[next_date], gap))
                continue  # used (whether or not a valid next-day M15 bar existed)
            still_active.append(gap)
        active = still_active
    return sorted(entries, key=lambda e: e[0])


def simulate(m15, entries):
    o, h, l, c = m15["open"].values, m15["high"].values, m15["low"].values, m15["close"].values
    t = m15["time"].values
    m = len(m15)

    equity = START_EQUITY
    equity_curve = [equity]
    trades = []
    i = 0
    for entry_idx, gap in entries:
        if entry_idx < i:
            continue  # already in a trade, one at a time
        buffer = ATR_BUFFER_MULT * gap.get("_atr", 0.0)
        stop = gap["bottom"] - buffer
        entry_price = o[entry_idx]
        r = entry_price - stop
        if r <= 0:
            continue
        target = entry_price + TARGET_RR * r

        if SIZING_MODE == "fixed_units":
            qty = FIXED_UNITS
        else:
            qty = min((equity * RISK_PCT) / r, (equity * MAX_LEVERAGE) / entry_price)
        risk_amount = qty * r  # actual $ at stake on this trade -- correct basis for R-multiples
        entry_commission = qty * entry_price * COMMISSION_RATE
        equity -= entry_commission

        exit_price = exit_idx = exit_reason = None
        for k in range(entry_idx, m):
            if o[k] <= stop and k > entry_idx:
                exit_price, exit_idx, exit_reason = o[k], k, "gap_stop"
                break
            if l[k] <= stop:
                exit_price, exit_idx, exit_reason = stop, k, "stop"
                break
            if h[k] >= target:
                exit_price, exit_idx, exit_reason = target, k, "target"
                break
        if exit_price is None:
            exit_price, exit_idx, exit_reason = c[m - 1], m - 1, "data_end"

        pnl = qty * (exit_price - entry_price) - abs(qty * exit_price) * COMMISSION_RATE
        equity += pnl
        equity_curve.append(equity)
        trades.append({
            "entry_idx": entry_idx, "entry_time": t[entry_idx], "entry_price": entry_price,
            "stop": stop, "target": target,
            "exit_time": t[exit_idx], "exit_price": exit_price, "exit_reason": exit_reason,
            "pnl": pnl, "r_multiple": pnl / risk_amount if risk_amount else np.nan,
        })
        i = exit_idx + 1

    return pd.DataFrame(trades), equity_curve


def summarize(trades, curve, symbol):
    print(f"\n=== {symbol} (Daily FVG retracement, next-day-open entry) ===")
    n = len(trades)
    if n == 0:
        print("No trades.")
        return
    wins = (trades["pnl"] > 0).sum()
    profit = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    loss = -trades.loc[trades["pnl"] < 0, "pnl"].sum()
    vals = np.asarray(curve)
    peak = np.maximum.accumulate(vals)
    print(f"  trades          : {n}")
    print(f"  win_rate        : {100*wins/n:.1f}%")
    print(f"  avg_r           : {trades['r_multiple'].mean():.3f}")
    print(f"  profit_factor   : {profit/loss if loss else float('nan'):.3f}")
    print(f"  max_drawdown    : {(((vals-peak)/peak).min()*100):.2f}%")
    print(f"  final_equity    : ${curve[-1]:,.2f}")
    print(f"  total_return    : {100*(curve[-1]/START_EQUITY-1):.2f}%")
    from collections import Counter
    print(f"  exit reasons    : {dict(Counter(trades['exit_reason']))}")


def main():
    for symbol in SYMBOLS:
        print(f"\nFetching {symbol} D1 + M15 {START.date()} -> {END.date()} ...")
        d1 = fetch(symbol, mt5.TIMEFRAME_D1, START, END)
        m15 = fetch(symbol, mt5.TIMEFRAME_M15, START, END)
        print(f"  {len(d1)} D1 bars, {len(m15)} M15 bars")

        atr = compute_atr14(d1).values
        entries = find_entries(d1, m15)
        for _, gap in entries:
            gap["_atr"] = atr[gap["formed_at"]] if not np.isnan(atr[gap["formed_at"]]) else 0.0
        print(f"  bullish FVG entries triggered: {len(entries)}")

        trades, curve = simulate(m15, entries)
        summarize(trades, curve, symbol)


if __name__ == "__main__":
    main()
