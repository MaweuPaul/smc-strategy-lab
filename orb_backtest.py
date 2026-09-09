"""
Opening Range Breakout (ORB) backtest.

For each UTC trading day:
  1. Mark the opening range = the first `range_minutes` after a chosen
     session open time (UTC), using M1 bars for precision.
  2. After the range forms, watch for the first M1 close beyond either
     side of the range within a monitoring window.
  3. Enter in the breakout direction at the next bar's open.
  4. Stop at the opposite side of the range.
  5. Target is either RR-based (target_mode="rr") or a measured-move
     multiple of the opening range's own width (target_mode="measured_move",
     e.g. the LuxAlgo ORB indicator's target style), which tends to fit a
     noisy short opening range better than a flat RR.
  6. Optional day-over-day bias filter (like LuxAlgo's "Daily Bias"): only
     take a breakout whose direction agrees with today's opening-range
     midpoint vs. yesterday's (a cheap trend proxy, no separate HTF chart
     needed).

One trade per day (first valid breakout only). Data is read-only from a
running MetaTrader5 terminal; no orders are ever placed.
"""

import argparse
from datetime import timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import MetaTrader5 as mt5

from smc_backtest import TIMEFRAMES
from htf_ltf_backtest import resolve_target, simulate_exit, summarize

NY_TZ = ZoneInfo("America/New_York")


def fetch_m1(symbol: str, bars: int) -> pd.DataFrame:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
    if mt5.symbol_info(symbol) is None:
        raise RuntimeError(f"Symbol {symbol} not found")
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAMES["M1"], 0, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No data: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[["time", "open", "high", "low", "close"]].reset_index(drop=True)


def run_orb(symbol, bars=500000, session_open_hour=7, session_open_minute=0,
            range_minutes=3, monitor_hours=6, min_rr=1.5, max_rr=3.0,
            max_hold_bars=4320, target_mode="rr", target_range_mult=1.0,
            use_daily_bias=False):
    """max_hold_bars is in M1 bars -- default 4320 = 3 days. A trade that
    hasn't hit its stop or target by then is force-closed at whatever price
    it's at (marked win/loss by sign), which can distort results if too many
    trades hit this ceiling. Raise it further if you see a lot of timeouts."""
    m1 = fetch_m1(symbol, bars)
    print(f"M1: {len(m1)} bars ({m1['time'].iloc[0]} -> {m1['time'].iloc[-1]})")

    days = sorted(set(m1["time"].dt.floor("D")))
    trades = []
    blocked_until = pd.Timestamp.min.tz_localize("UTC")
    prev_mid = None

    for day in days:
        open_time = day + timedelta(hours=session_open_hour, minutes=session_open_minute)
        if open_time < blocked_until:
            continue
        range_end = open_time + timedelta(minutes=range_minutes)
        monitor_end = range_end + timedelta(hours=monitor_hours)

        range_mask = (m1["time"] >= open_time) & (m1["time"] < range_end)
        rng = m1.loc[range_mask]
        if len(rng) < range_minutes:
            continue
        range_high, range_low = rng["high"].max(), rng["low"].min()
        if range_high <= range_low:
            continue

        mid = (range_high + range_low) / 2
        bias_dir = None
        if use_daily_bias and prev_mid is not None:
            if mid > prev_mid:
                bias_dir = "bullish"
            elif mid < prev_mid:
                bias_dir = "bearish"
        prev_mid = mid

        post_mask = (m1["time"] >= range_end) & (m1["time"] <= monitor_end)
        post = m1.loc[post_mask].reset_index(drop=True)
        if len(post) < 2:
            continue

        direction, breakout_idx = None, None
        for i in range(len(post)):
            if post["close"][i] > range_high:
                direction, breakout_idx = "bullish", i
                break
            if post["close"][i] < range_low:
                direction, breakout_idx = "bearish", i
                break
        if direction is None:
            continue
        if use_daily_bias and bias_dir is not None and direction != bias_dir:
            continue

        entry_local = breakout_idx + 1
        if entry_local >= len(post):
            continue
        entry_time = post["time"][entry_local]
        gi = m1.index[m1["time"] == entry_time]
        if len(gi) == 0:
            continue
        gi = gi[0]
        entry_price = m1["open"][gi]

        if direction == "bullish":
            stop = range_low
            risk = entry_price - stop
        else:
            stop = range_high
            risk = stop - entry_price
        if risk <= 0:
            continue

        if target_mode == "measured_move":
            range_width = range_high - range_low
            move = target_range_mult * range_width
            target_price = entry_price + move if direction == "bullish" else entry_price - move
            rr_target = move / risk
        else:
            target_price, rr_target = resolve_target([], direction, entry_price, risk, min_rr, max_rr)

        outcome, exit_price, exit_idx, r = simulate_exit(
            m1, gi, entry_price, stop, target_price, rr_target, direction, max_hold_bars, risk)

        trades.append({
            "day": day, "direction": "long" if direction == "bullish" else "short",
            "bias_dir": bias_dir,
            "range_high": range_high, "range_low": range_low,
            "range_start": open_time, "range_end": range_end,
            "entry_time": entry_time, "exit_time": m1["time"][exit_idx],
            "entry": entry_price, "stop": stop, "target": target_price,
            "exit_price": exit_price, "outcome": outcome, "r_multiple": r,
            "rr_planned": rr_target,
        })
        blocked_until = m1["time"][exit_idx]

    return pd.DataFrame(trades)


def run_orb_ny_open(symbol, bars=500000, candle_minutes=1, monitor_hours=6,
                     min_rr=1.5, max_rr=3.0, max_hold_bars=4320,
                     target_mode="rr", target_range_mult=1.0, use_daily_bias=False):
    """NY-open opening range: mark the single candle (or last `candle_minutes`
    of M1 bars) immediately BEFORE the 9:30am New York open, then trade the
    first M1 close beyond its high (long) or low (short) after the open.

    This is a different rule from run_orb() above, which marks a range for
    N minutes AFTER a fixed UTC session-open hour. Two things that matters
    here specifically because the anchor is "New York open":
      - 9:30am NY is DST-dependent (13:30 UTC in winter/EST, 14:30 UTC in
        summer/EDT) -- a fixed UTC hour would be wrong on one side of the
        clock change every year. Grouping and windowing is done entirely in
        America/New_York local time via zoneinfo, which handles this
        correctly across the whole fetched history.
      - The marking window is BEFORE the open, not after it -- the trade
        thesis is "does price take out the level printed into the open,"
        not "does it break the first N minutes of the session."

    Same execution conventions as run_orb(): entry at the open of the bar
    AFTER the breakout close, stop at the opposite side of the marking
    range, one trade per day (first valid breakout only), optional
    day-over-day bias filter on the marking range's own midpoint.
    """
    m1 = fetch_m1(symbol, bars)
    print(f"M1: {len(m1)} bars ({m1['time'].iloc[0]} -> {m1['time'].iloc[-1]})")
    m1["time_ny"] = m1["time"].dt.tz_convert(NY_TZ)

    days = sorted(set(m1["time_ny"].dt.floor("D")))
    trades = []
    blocked_until = pd.Timestamp.min.tz_localize("UTC")
    prev_mid = None

    for day in days:
        open_time = day.replace(hour=9, minute=30, second=0, microsecond=0)
        if open_time < blocked_until:
            continue
        range_start = open_time - timedelta(minutes=candle_minutes)
        monitor_end = open_time + timedelta(hours=monitor_hours)

        range_mask = (m1["time_ny"] >= range_start) & (m1["time_ny"] < open_time)
        rng = m1.loc[range_mask]
        if len(rng) < candle_minutes:
            continue  # market closed / no data into this open (holiday, weekend)
        range_high, range_low = rng["high"].max(), rng["low"].min()
        if range_high <= range_low:
            continue

        mid = (range_high + range_low) / 2
        bias_dir = None
        if use_daily_bias and prev_mid is not None:
            if mid > prev_mid:
                bias_dir = "bullish"
            elif mid < prev_mid:
                bias_dir = "bearish"
        prev_mid = mid

        post_mask = (m1["time_ny"] >= open_time) & (m1["time_ny"] <= monitor_end)
        post = m1.loc[post_mask].reset_index(drop=True)
        if len(post) < 2:
            continue

        direction, breakout_idx = None, None
        for i in range(len(post)):
            if post["close"][i] > range_high:
                direction, breakout_idx = "bullish", i
                break
            if post["close"][i] < range_low:
                direction, breakout_idx = "bearish", i
                break
        if direction is None:
            continue
        if use_daily_bias and bias_dir is not None and direction != bias_dir:
            continue

        entry_local = breakout_idx + 1
        if entry_local >= len(post):
            continue
        entry_time = post["time"][entry_local]
        gi = m1.index[m1["time"] == entry_time]
        if len(gi) == 0:
            continue
        gi = gi[0]
        entry_price = m1["open"][gi]

        if direction == "bullish":
            stop = range_low
            risk = entry_price - stop
        else:
            stop = range_high
            risk = stop - entry_price
        if risk <= 0:
            continue

        if target_mode == "measured_move":
            range_width = range_high - range_low
            move = target_range_mult * range_width
            target_price = entry_price + move if direction == "bullish" else entry_price - move
            rr_target = move / risk
        else:
            target_price, rr_target = resolve_target([], direction, entry_price, risk, min_rr, max_rr)

        outcome, exit_price, exit_idx, r = simulate_exit(
            m1, gi, entry_price, stop, target_price, rr_target, direction, max_hold_bars, risk)

        trades.append({
            "day": day.tz_localize(None), "direction": "long" if direction == "bullish" else "short",
            "bias_dir": bias_dir,
            "range_high": range_high, "range_low": range_low,
            "range_start": range_start, "range_end": open_time,
            "entry_time": entry_time, "exit_time": m1["time"][exit_idx],
            "entry": entry_price, "stop": stop, "target": target_price,
            "exit_price": exit_price, "outcome": outcome, "r_multiple": r,
            "rr_planned": rr_target,
        })
        blocked_until = m1["time"][exit_idx]

    return pd.DataFrame(trades)


def main():
    ap = argparse.ArgumentParser(description="Opening Range Breakout backtest")
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--bars", type=int, default=500000)
    ap.add_argument("--open-hour", type=int, default=7)
    ap.add_argument("--open-minute", type=int, default=0)
    ap.add_argument("--range-minutes", type=int, default=3)
    ap.add_argument("--min-rr", type=float, default=1.5)
    ap.add_argument("--max-rr", type=float, default=3.0)
    ap.add_argument("--max-hold-bars", type=int, default=4320, help="M1 bars; 4320 = 3 days")
    ap.add_argument("--target-mode", default="rr", choices=["rr", "measured_move"])
    ap.add_argument("--target-range-mult", type=float, default=1.0)
    ap.add_argument("--daily-bias", action="store_true")
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--out", default="orb_trades.csv")
    args = ap.parse_args()

    trades = run_orb(args.symbol, args.bars, args.open_hour, args.open_minute,
                      args.range_minutes, min_rr=args.min_rr, max_rr=args.max_rr,
                      max_hold_bars=args.max_hold_bars, target_mode=args.target_mode,
                      target_range_mult=args.target_range_mult, use_daily_bias=args.daily_bias)
    if not trades.empty:
        trades.to_csv(args.out, index=False)
        print(f"Saved {len(trades)} trades to {args.out}")
    summarize(trades, risk_pct=args.risk_pct)


if __name__ == "__main__":
    main()
