"""
RESULT: THIS IDEA DOES NOT WORK. Kept for the record, off by default in
the panel. An earlier version of this file filled leg 1 at the OPEN of
the bar whose LOW triggered the entry -- look-ahead, since the low isn't
known until that bar closes, and the open sits above the trigger level
~99% of the time (mean ~$10.9 on Gold, ~56pts on USTEC). That fake head
start was the entire edge: it showed +0.65R/short on Gold and +0.64R on
USTEC. Filling honestly at the trigger level (a resting sell-limit, see
simulate_bidirectional) flips every symbol negative -- Gold -0.24R,
USTEC -0.62R, GBPUSD -0.70R, EURUSD -0.87R. The long-only retracement
strategy in daily_fvg_newday.py audited clean and is unaffected.

Adds a "ride to the magnet" short leg on top of the existing daily FVG
retracement strategy (daily_fvg_newday.py), which is otherwise UNCHANGED.

For every bullish FVG:
  Leg 1 (NEW) -- do NOT short just because the gap formed. Instead, watch
    the gap as a magnet and wait for price to actually trade back down
    INTO its range (M15 low touches the gap's top edge) before taking
    any entry -- "if price is going to an FVG we can look for entries in
    that range, we don't trade the FVG alone." Once price taps the top
    of the range, short it, betting the fill continues through to the
    bottom. Target = the gap's BOTTOM (full fill). Stop = entry +
    ATR_BUFFER_MULT x D1 ATR14 (same buffer convention as the rest of
    this project, just placed above entry since there's no swept level
    to anchor to here). If price never comes back to the gap within the
    lookahead window, no leg-1 trade is taken for that gap at all.
  Leg 2 (EXISTING, untouched) -- once the gap is actually entered
    (whether or not leg 1 was running), the original retracement-buy
    fires exactly as in daily_fvg_newday.py: buy at the next day's open,
    stop at gap bottom - buffer, fixed-RR target.

Leg 1 and leg 2 are two independent entries per gap, merged into one
time-sorted stream and run through a single position-at-a-time
simulator (a short and a long can't be open simultaneously) -- so in
practice leg 1 typically closes (hitting its target at the gap) right
around when leg 2 would want to open, letting the strategy flip from
riding the approach down to riding the bounce back up.

ASSUMPTIONS (flagged, not tuned):
  - Leg 1 only fires for gaps price actually revisits within
    LOOKAHEAD_DAYS trading days of formation; gaps price never comes
    back to simply produce no leg-1 trade (this is the fix for the
    earlier version, which shorted every single gap unconditionally).
  - Entry is taken at the OPEN of the M15 bar whose low first touches
    the gap's top (a proxy for "filled at the touch," consistent with
    the rest of this project always executing at a bar's open rather
    than modeling a resting limit order).
  - Leg 1's stop is a flat ATR offset above entry (no structural level
    used, unlike every other stop in this project) -- the simplest
    thing that could work; worth revisiting if this leg's results are
    promising enough to invest more in.
"""


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # repo root, for shared modules (smc_backtest, etc.)

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from smc_backtest import find_fvgs
from daily_fvg_newday import fetch, find_entries, SYMBOLS, START, END
from p404_sweep_reversal import compute_atr14
import daily_fvg_newday as fvg_mod

LOOKAHEAD_DAYS = 60  # how long we'll wait for price to come back to a gap


def find_leg1_short_entries(d1, m15):
    fvgs = find_fvgs(d1)
    only_bullish = [g for g in fvgs if g["type"] == "bullish"]
    d1_date = d1["time"].dt.date.values
    m15_date = m15["time"].dt.date.values
    m15_low = m15["low"].values
    n = len(d1)

    first_m15_idx_for_date = {}
    for i, dt in enumerate(m15_date):
        if dt not in first_m15_idx_for_date:
            first_m15_idx_for_date[dt] = i

    entries = []
    for gap in only_bullish:
        start_idx = gap["formed_at"] + 1
        if start_idx >= n:
            continue
        end_idx = min(start_idx + LOOKAHEAD_DAYS, n - 1)
        start_date = d1_date[start_idx]
        end_date = d1_date[end_idx]
        if start_date not in first_m15_idx_for_date:
            continue
        m15_start = first_m15_idx_for_date[start_date]
        m15_end = first_m15_idx_for_date.get(end_date, len(m15) - 1)

        touch_idx = None
        for k in range(m15_start, m15_end + 1):
            if m15_low[k] <= gap["top"]:
                touch_idx = k
                break
        if touch_idx is None:
            continue  # price never came back to this gap -- no trade
        entries.append((touch_idx, gap))
    return entries


def simulate_bidirectional(m15, leg1_entries, leg2_entries, leg1_risk_mult=0.5):
    """leg1_entries: (m15_idx, gap) -- SHORT, target=gap bottom (full fill), stop=entry+buffer.
    leg2_entries: (m15_idx, gap) -- LONG, stop=gap bottom-buffer, target=fixed RR.
    Merged into one time-sorted, one-position-at-a-time stream.

    leg1_risk_mult sizes the short leg relative to the long leg's normal
    RISK_PCT. Default 0.5 -- halving the short's size meaningfully cuts
    max drawdown (e.g. USTEC -22.8% -> -17.7%, XAUUSD -13.4% -> -9.9%)
    with no cost (often a small gain) to avg R, since it's the newer,
    less-structurally-anchored leg (flat ATR stop, no swept level)."""
    o, h, l, c = m15["open"].values, m15["high"].values, m15["low"].values, m15["close"].values
    t = m15["time"].values
    m = len(m15)

    unified = []
    for idx, gap in leg1_entries:
        unified.append((idx, "short", gap))
    for idx, gap in leg2_entries:
        unified.append((idx, "long", gap))
    unified.sort(key=lambda e: e[0])

    equity = fvg_mod.START_EQUITY
    equity_curve = [equity]
    trades = []
    i = 0
    for entry_idx, direction, gap in unified:
        if entry_idx < i:
            continue
        sign = 1 if direction == "long" else -1
        buffer = fvg_mod.ATR_BUFFER_MULT * gap.get("_atr", 0.0)
        if direction == "long":
            entry_price = o[entry_idx]
            stop = gap["bottom"] - buffer
            target = entry_price + fvg_mod.TARGET_RR * (entry_price - stop)
        else:
            # Leg 1's trigger is "price traded down into the gap", detected via
            # this bar's LOW -- which isn't known until the bar closes. Filling
            # at the bar's OPEN would be look-ahead: the open sits above the
            # trigger level ~99% of the time (mean ~$10.9 on Gold, ~56pts on
            # USTEC), handing the short a free head start it could never get
            # live. Model it as what it actually is -- a resting sell-limit at
            # the gap's top edge -- so we fill AT the level, or worse (at the
            # open) if price gapped straight through it.
            entry_price = min(o[entry_idx], gap["top"])
            stop = entry_price + buffer
            target = gap["bottom"]
        r = sign * (entry_price - stop)
        if r <= 0:
            continue

        size_mult = leg1_risk_mult if direction == "short" else 1.0
        if fvg_mod.SIZING_MODE == "fixed_units":
            qty = fvg_mod.FIXED_UNITS * size_mult
        else:
            risk_pct = fvg_mod.RISK_PCT * size_mult
            qty = min((equity * risk_pct) / r, (equity * fvg_mod.MAX_LEVERAGE) / entry_price)
        risk_amount = qty * r  # actual $ at stake on this trade -- correct basis for R-multiples
        entry_commission = qty * entry_price * fvg_mod.COMMISSION_RATE
        equity -= entry_commission

        exit_price = exit_idx = exit_reason = None
        for k in range(entry_idx, m):
            gapped = (o[k] >= stop) if direction == "short" else (o[k] <= stop)
            if gapped and k > entry_idx:
                exit_price, exit_idx, exit_reason = o[k], k, "gap_stop"
                break
            stop_hit = (h[k] >= stop) if direction == "short" else (l[k] <= stop)
            if stop_hit:
                exit_price, exit_idx, exit_reason = stop, k, "stop"
                break
            target_hit = (l[k] <= target) if direction == "short" else (h[k] >= target)
            if target_hit:
                exit_price, exit_idx, exit_reason = target, k, "target"
                break
        if exit_price is None:
            exit_price, exit_idx, exit_reason = c[m - 1], m - 1, "data_end"

        pnl = sign * qty * (exit_price - entry_price) - abs(qty * exit_price) * fvg_mod.COMMISSION_RATE
        equity += pnl
        equity_curve.append(equity)
        trades.append({
            "direction": direction, "entry_idx": entry_idx, "entry_time": t[entry_idx], "entry_price": entry_price,
            "stop": stop, "target": target, "exit_time": t[exit_idx], "exit_price": exit_price,
            "exit_reason": exit_reason, "pnl": pnl,
            "r_multiple": pnl / risk_amount if risk_amount else np.nan,
        })
        i = exit_idx + 1

    return pd.DataFrame(trades), equity_curve


def summarize(trades, curve, symbol):
    print(f"\n=== {symbol} (ride-to-magnet + retracement) ===")
    n = len(trades)
    if n == 0:
        print("No trades.")
        return
    for direction in ("short", "long"):
        sub = trades[trades["direction"] == direction]
        if len(sub) == 0:
            continue
        wins = (sub["pnl"] > 0).sum()
        print(f"  [{direction}] n={len(sub)} win%={100*wins/len(sub):.1f} avgR={sub['r_multiple'].mean():+.3f}")
    wins = (trades["pnl"] > 0).sum()
    profit = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    loss = -trades.loc[trades["pnl"] < 0, "pnl"].sum()
    vals = np.asarray(curve)
    peak = np.maximum.accumulate(vals)
    print(f"  TOTAL n={n} win%={100*wins/n:.1f} avgR={trades['r_multiple'].mean():+.3f} "
          f"PF={(profit/loss if loss else float('nan')):.2f} "
          f"DD={(((vals-peak)/peak).min()*100):.1f}% final_eq=${curve[-1]:,.0f}")


def main():
    for symbol in SYMBOLS:
        print(f"\nFetching {symbol} D1 + M15 {START.date()} -> {END.date()} ...")
        d1 = fetch(symbol, mt5.TIMEFRAME_D1, START, END)
        m15 = fetch(symbol, mt5.TIMEFRAME_M15, START, END)

        atr = compute_atr14(d1).values
        leg1 = find_leg1_short_entries(d1, m15)
        leg2 = find_entries(d1, m15)
        for _, gap in leg1:
            gap["_atr"] = atr[gap["formed_at"]] if not np.isnan(atr[gap["formed_at"]]) else 0.0
        for _, gap in leg2:
            gap["_atr"] = atr[gap["formed_at"]] if not np.isnan(atr[gap["formed_at"]]) else 0.0
        print(f"  leg1 (short-to-magnet) candidates: {len(leg1)}  leg2 (retracement-buy) candidates: {len(leg2)}")

        trades, curve = simulate_bidirectional(m15, leg1, leg2)
        summarize(trades, curve, symbol)


if __name__ == "__main__":
    main()
