"""
Asian-session liquidity backtest.

For each UTC trading day:
  1. Mark the Asian session range (default 00:00-06:00 UTC).
  2. Check whether a recent minor swing gets swept (wick beyond, close back
     inside) DURING the Asian session, using the same sweep->MSS->OTE cascade
     as the HTF/LTF engine (find_confirmation), just scoped to a window that
     starts before the session and runs through the following killzones.
  3. If such a sweep occurred inside the Asian window -> trade the reversal:
     direction = opposite of the swept side, entry at the OTE pullback,
     stop beyond the sweep wick, RR-based target.
  4. If no sweep occurred in the Asian window -> trade continuation: bias =
     the session's own trend (close vs open). Wait for price to break the
     Asian range in that direction and then retest it (break-and-retest),
     entry on the retest during a killzone. Stop beyond the OPPOSITE side of
     the Asian range.

All entries are still filtered to London/NY killzones. Data is read-only
from a running MetaTrader5 terminal; no orders are ever placed.
"""


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # repo root, for shared modules (smc_backtest, etc.)

import argparse
from datetime import timedelta

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from smc_backtest import TIMEFRAMES, find_swings, structure_bias_series
from htf_ltf_backtest import (
    find_confirmation, in_killzone, resolve_target, simulate_exit,
    DEFAULT_KILLZONES, summarize,
)

DEFAULT_ASIAN_HOURS = (0, 6)  # UTC


def fetch_ltf_only(symbol: str, ltf_name: str, bars: int) -> pd.DataFrame:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
    if mt5.symbol_info(symbol) is None:
        raise RuntimeError(f"Symbol {symbol} not found")
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[ltf_name], 0, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No data: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[["time", "open", "high", "low", "close"]].reset_index(drop=True)


def build_htf_bias_lookup(symbol: str, htf_name: str, htf_bars: int):
    """HTF structure bias (bullish/bearish/neutral from HH-HL / LH-LL), queryable by timestamp."""
    htf = fetch_ltf_only(symbol, htf_name, htf_bars)
    swing_highs, swing_lows = find_swings(htf, window=2)
    bias_arr = structure_bias_series(htf, swing_highs, swing_lows, swing_window=2)
    times = htf["time"]

    def bias_at(t):
        idx = times.searchsorted(t, side="right") - 1
        if idx < 0:
            return "neutral"
        return bias_arr[idx]

    return bias_at


def run_asian(symbol, ltf_name="M15", bars=20000, asian_hours=DEFAULT_ASIAN_HOURS,
              killzones=DEFAULT_KILLZONES, min_rr=3.0, max_rr=5.0,
              lookback_hours=24, monitor_hours=20, max_hold_bars=200,
              htf_name=None, htf_bars=3000, allow_neutral_bias=True):
    """
    htf_name: if set (e.g. "H4" or "D1"), only take a trade whose direction
    agrees with the HTF structure bias (HH+HL=bullish, LH+LL=bearish) as of
    the start of that day's Asian session. allow_neutral_bias controls
    whether a "neutral" HTF reading (no clear bias yet) still lets trades
    through, or blocks them too.
    """
    ltf = fetch_ltf_only(symbol, ltf_name, bars)
    print(f"{ltf_name}: {len(ltf)} bars ({ltf['time'].iloc[0]} -> {ltf['time'].iloc[-1]})")

    bias_at = build_htf_bias_lookup(symbol, htf_name, htf_bars) if htf_name else None

    days = sorted(set(ltf["time"].dt.floor("D")))
    trades = []
    blocked_until = pd.Timestamp.min.tz_localize("UTC")

    for day in days:
        asian_start = day + timedelta(hours=asian_hours[0])
        asian_end = day + timedelta(hours=asian_hours[1])
        if asian_start < blocked_until:
            continue

        asian_mask = (ltf["time"] >= asian_start) & (ltf["time"] < asian_end)
        asian = ltf.loc[asian_mask]
        if len(asian) < 4:
            continue
        asian_high, asian_low = asian["high"].max(), asian["low"].min()
        asian_open, asian_close = asian["open"].iloc[0], asian["close"].iloc[-1]

        window_start = asian_start - timedelta(hours=lookback_hours)
        window_end = asian_start + timedelta(hours=monitor_hours)
        win_mask = (ltf["time"] >= window_start) & (ltf["time"] <= window_end)
        win = ltf.loc[win_mask].reset_index(drop=True)
        if len(win) < 10:
            continue

        # --- 1. look for an Asian-session sweep (either direction) ---
        setup, direction, mode = None, None, None
        for cand_dir in ("bullish", "bearish"):
            s = find_confirmation(win, cand_dir)
            if s is None:
                continue
            sweep_time = win["time"][s["sweep_idx"]]
            if asian_start <= sweep_time < asian_end:
                if setup is None or s["sweep_idx"] < setup["sweep_idx"]:
                    setup, direction, mode = s, cand_dir, "reversal"

        entry_time = entry_price = stop = target_price = rr_target = None
        sweep_time = mss_time = ote_top = ote_bottom = sweep_extreme = None

        if setup is not None:
            entry_local = setup["entry_idx"]
            entry_time = win["time"][entry_local]
            if not in_killzone(entry_time, killzones):
                setup = None  # fall through to continuation check below
            else:
                gi = ltf.index[ltf["time"] == entry_time]
                if len(gi) == 0:
                    setup = None
                else:
                    gi = gi[0]
                    entry_price = ltf["open"][gi + 1] if gi + 1 < len(ltf) else ltf["close"][gi]
                    sweep_extreme = setup["sweep_extreme"]
                    ote_top, ote_bottom = setup["ote_top"], setup["ote_bottom"]
                    if direction == "bullish":
                        stop = sweep_extreme - (ote_top - ote_bottom) * 0.1
                        risk = entry_price - stop
                    else:
                        stop = sweep_extreme + (ote_top - ote_bottom) * 0.1
                        risk = stop - entry_price
                    if risk <= 0:
                        setup = None
                    else:
                        target_price, rr_target = resolve_target([], direction, entry_price, risk, min_rr, max_rr)
                        sweep_time, mss_time = win["time"][setup["sweep_idx"]], win["time"][setup["mss_idx"]]

        if setup is None:
            # --- 2. no valid Asian sweep -> follow the session's own trend ---
            mode = "continuation"
            direction = "bullish" if asian_close >= asian_open else "bearish"
            post_mask = (ltf["time"] >= asian_end) & (ltf["time"] <= window_end)
            post = ltf.loc[post_mask].reset_index(drop=True)
            if len(post) < 4:
                continue

            if direction == "bullish":
                breakout_idx = next((i for i in range(len(post)) if post["close"][i] > asian_high), None)
            else:
                breakout_idx = next((i for i in range(len(post)) if post["close"][i] < asian_low), None)
            if breakout_idx is None:
                continue

            retest_idx = None
            for i in range(breakout_idx + 1, len(post)):
                if direction == "bullish" and post["low"][i] <= asian_high:
                    retest_idx = i
                    break
                if direction == "bearish" and post["high"][i] >= asian_low:
                    retest_idx = i
                    break
            if retest_idx is None:
                continue

            entry_time = post["time"][retest_idx]
            if not in_killzone(entry_time, killzones):
                continue
            gi = ltf.index[ltf["time"] == entry_time]
            if len(gi) == 0:
                continue
            gi = gi[0]
            entry_price = ltf["open"][gi + 1] if gi + 1 < len(ltf) else ltf["close"][gi]

            if direction == "bullish":
                stop = asian_low
                risk = entry_price - stop
            else:
                stop = asian_high
                risk = stop - entry_price
            if risk <= 0:
                continue
            target_price, rr_target = resolve_target([], direction, entry_price, risk, min_rr, max_rr)
        else:
            gi = ltf.index[ltf["time"] == entry_time][0]

        htf_bias = bias_at(asian_start) if bias_at is not None else None
        if htf_bias is not None:
            if htf_bias == "neutral":
                if not allow_neutral_bias:
                    continue
            elif htf_bias != direction:
                continue

        outcome, exit_price, exit_idx, r = simulate_exit(
            ltf, gi + 1, entry_price, stop, target_price, rr_target, direction, max_hold_bars, risk)

        trades.append({
            "day": day, "mode": mode, "direction": "long" if direction == "bullish" else "short",
            "htf_bias": htf_bias,
            "asian_high": asian_high, "asian_low": asian_low,
            "asian_open": asian_open, "asian_close": asian_close,
            "sweep_time": sweep_time, "mss_time": mss_time,
            "ote_top": ote_top, "ote_bottom": ote_bottom, "sweep_extreme": sweep_extreme,
            "entry_time": entry_time, "exit_time": ltf["time"][exit_idx],
            "entry": entry_price, "stop": stop, "target": target_price,
            "exit_price": exit_price, "outcome": outcome, "r_multiple": r,
            "rr_planned": rr_target,
        })
        blocked_until = ltf["time"][exit_idx]

    return pd.DataFrame(trades)


def main():
    ap = argparse.ArgumentParser(description="Asian-session liquidity backtest")
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--ltf", default="M15", choices=TIMEFRAMES.keys())
    ap.add_argument("--bars", type=int, default=20000)
    ap.add_argument("--asian-start", type=int, default=0)
    ap.add_argument("--asian-end", type=int, default=6)
    ap.add_argument("--min-rr", type=float, default=3.0)
    ap.add_argument("--max-rr", type=float, default=5.0)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--out", default="asian_trades.csv")
    args = ap.parse_args()

    trades = run_asian(args.symbol, args.ltf, args.bars,
                        asian_hours=(args.asian_start, args.asian_end),
                        min_rr=args.min_rr, max_rr=args.max_rr)
    if not trades.empty:
        trades.to_csv(args.out, index=False)
        print(f"Saved {len(trades)} trades to {args.out}")
        print()
        print("By mode:")
        print(trades.groupby("mode")["r_multiple"].agg(["count", "mean"]))
        print()
    summarize(trades, risk_pct=args.risk_pct)


if __name__ == "__main__":
    main()
