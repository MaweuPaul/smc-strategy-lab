"""
Support/Resistance + Liquidity backtest -- the stripped-down version with no
OTE/MSS/FVG cascade at all.

  1. Identify significant S/R levels: HTF swing highs/lows (fractal, wider
     window than the other engines here, so only genuinely significant
     pivots count).
  2. Once a level is confirmed, watch the LTF for the first liquidity sweep
     of it in EITHER direction: a wick through the level that closes back on
     the origin side (rejection). A level can act as resistance (rejected
     from below) or support (rejected from above) regardless of which type
     of swing originally formed it -- that's classic S/R role-reversal.
  3. Two selectable entry modes once a level is engaged:
       "reversal" (default): enter in the rejection direction right on the
       sweep/reject bar, or optionally wait for a retest of the level
       (require_retest=True) before entering, for a tighter stop.
       "breakout": instead of fading the level, require a full-bodied
       candle (body >= body_frac of its range -- a decisive close, not a
       small wick) to close beyond it on above-average tick volume, and
       trade the continuation through the level.
  4. Stop: reversal mode stops beyond the sweep wick; breakout mode stops
     back inside the broken level. Target = the next opposing S/R level
     (any other confirmed level on the correct side of price), RR-clipped;
     falls back to a flat RR if none qualifies.

One trade per level. Entries filtered to London/NY killzones. Data is
read-only from a running MetaTrader5 terminal; no orders are ever placed.
"""

import argparse
from datetime import timedelta

import pandas as pd
import MetaTrader5 as mt5

from smc_backtest import find_swings, TIMEFRAMES
from htf_ltf_backtest import (
    HTF_TO_LTF, DEFAULT_KILLZONES, fetch_htf_ltf, in_killzone,
    simulate_exit, simulate_exit_partial, summarize,
)


def fetch_ltf_with_volume(symbol: str, ltf_name: str, start, end) -> pd.DataFrame:
    rates = mt5.copy_rates_range(symbol, TIMEFRAMES[ltf_name], start, end)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No LTF volume data: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df[["time", "open", "high", "low", "close", "tick_volume"]].rename(columns={"tick_volume": "volume"})
    return df.reset_index(drop=True)


_M1_CACHE = {}


def fetch_m1_range(symbol: str, start, end) -> pd.DataFrame:
    """Small time-bounded M1 pull, used to resolve one trade's exit at
    finer granularity than its entry timeframe. Cached per (symbol, start,
    end) since the same window can be requested more than once."""
    key = (symbol, start, end)
    if key in _M1_CACHE:
        return _M1_CACHE[key]
    rates = mt5.copy_rates_range(symbol, TIMEFRAMES["M1"], start, end)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No M1 data for {symbol} {start}-{end}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df[["time", "open", "high", "low", "close"]].reset_index(drop=True)
    _M1_CACHE[key] = df
    return df


def simulate_exit_m1(symbol, ltf, entry_idx, entry_price, stop, target_price, rr_target, direction,
                      max_hold_ltf_bars, risk, ltf_bar_minutes,
                      breakeven_trigger_r=None, breakeven_buffer_r=0.0,
                      partial_r=None, partial_fraction=0.5, move_stop_to_be_after_partial=True):
    """
    Resolve stop/target/breakeven/partial order on M1 data instead of the
    LTF bars, to remove the "which happened first within this bar" guessing
    that a coarser candle forces. Falls back to the LTF-based simulate_exit*
    if M1 history isn't available for this window (e.g. very old dates).
    """
    entry_time = ltf["time"][entry_idx]
    window_end = entry_time + timedelta(minutes=ltf_bar_minutes * max_hold_ltf_bars)
    try:
        m1 = fetch_m1_range(symbol, entry_time, window_end)
    except RuntimeError:
        if partial_r is not None:
            return simulate_exit_partial(ltf, entry_idx, entry_price, stop, target_price, rr_target, direction,
                                          max_hold_ltf_bars, risk, partial_r, partial_fraction,
                                          move_stop_to_be_after_partial)
        outcome, exit_price, exit_idx, r = simulate_exit(
            ltf, entry_idx, entry_price, stop, target_price, rr_target, direction, max_hold_ltf_bars, risk,
            breakeven_trigger_r, breakeven_buffer_r)
        return outcome, r, exit_idx, None

    m1_entries = m1.index[m1["time"] >= entry_time]
    if len(m1_entries) == 0:
        raise RuntimeError("No M1 bars at/after entry_time")
    m1_entry_idx = m1_entries[0]
    m1_max_hold = len(m1) - m1_entry_idx - 1

    if partial_r is not None:
        outcome, r, m1_exit_idx, partial_taken = simulate_exit_partial(
            m1, m1_entry_idx, entry_price, stop, target_price, rr_target, direction, m1_max_hold, risk,
            partial_r, partial_fraction, move_stop_to_be_after_partial)
        exit_time = m1["time"][m1_exit_idx]
        return outcome, r, exit_time, partial_taken

    outcome, exit_price, m1_exit_idx, r = simulate_exit(
        m1, m1_entry_idx, entry_price, stop, target_price, rr_target, direction, m1_max_hold, risk,
        breakeven_trigger_r, breakeven_buffer_r)
    exit_time = m1["time"][m1_exit_idx]
    return outcome, r, exit_time, None


def build_levels(htf: pd.DataFrame, swing_window: int = 3):
    swing_highs, swing_lows = find_swings(htf, window=swing_window)
    levels = []
    for i in swing_highs:
        levels.append({"price": htf["high"][i], "formed_idx": i + swing_window, "kind": "high"})
    for i in swing_lows:
        levels.append({"price": htf["low"][i], "formed_idx": i + swing_window, "kind": "low"})
    levels.sort(key=lambda l: l["formed_idx"])
    return levels


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def build_leg_levels(htf: pd.DataFrame, swing_window: int = 3, atr_period: int = 14,
                      min_leg_atr_mult: float = 2.0):
    """
    Zigzag-style leg extraction: walk the confirmed swing points in order and
    only keep one as a genuine leg boundary if the move from the last kept
    boundary is at least min_leg_atr_mult x ATR. Smaller swings (consolidation
    chop within an ongoing leg) are treated as noise and dropped rather than
    becoming their own S/R level -- keeps only the previous major highs/lows.
    """
    swing_highs, swing_lows = find_swings(htf, window=swing_window)
    atr = compute_atr(htf, atr_period)

    points = [(i + swing_window, htf["high"][i], "high") for i in swing_highs]
    points += [(i + swing_window, htf["low"][i], "low") for i in swing_lows]
    points.sort(key=lambda p: p[0])

    legs = []
    last_kept = None  # (formed_idx, price, kind)
    for formed_idx, price, kind in points:
        if formed_idx >= len(htf):
            continue
        threshold = atr[formed_idx]
        if pd.isna(threshold):
            continue
        if last_kept is None:
            last_kept = (formed_idx, price, kind)
            legs.append({"price": price, "formed_idx": formed_idx, "kind": kind})
            continue
        if kind == last_kept[2]:
            # same-direction swing before a reversal -- keep only the more extreme one
            more_extreme = price > last_kept[1] if kind == "high" else price < last_kept[1]
            if more_extreme:
                legs[-1] = {"price": price, "formed_idx": formed_idx, "kind": kind}
                last_kept = (formed_idx, price, kind)
            continue
        move = abs(price - last_kept[1])
        if move >= min_leg_atr_mult * threshold:
            legs.append({"price": price, "formed_idx": formed_idx, "kind": kind})
            last_kept = (formed_idx, price, kind)
        # else: noise swing within the current leg -- drop it entirely
    legs.sort(key=lambda l: l["formed_idx"])
    return legs


def find_level_sweep(ltf: pd.DataFrame, level_price: float, start_idx: int, end_idx: int):
    """
    First bar in [start_idx, end_idx] that wicks through level_price and
    closes back on the side it came from. Returns (direction, sweep_idx) or
    None.

    Approached from below, wicks above, closes back below -> level held as
    RESISTANCE -> bearish (short). Approached from above, wicks below,
    closes back above -> level held as SUPPORT -> bullish (long).
    """
    highs, lows, closes = ltf["high"].values, ltf["low"].values, ltf["close"].values
    approached_from = "below" if closes[start_idx] < level_price else "above"
    for i in range(start_idx, min(end_idx, len(ltf) - 1) + 1):
        if approached_from == "below" and highs[i] > level_price and closes[i] < level_price:
            return "bearish", i
        if approached_from == "above" and lows[i] < level_price and closes[i] > level_price:
            return "bullish", i
        approached_from = "below" if closes[i] < level_price else "above"
    return None


def find_retest_entry(ltf: pd.DataFrame, level_price: float, sweep_idx: int, direction: str,
                       buffer: float, monitor_bars: int = 200, min_extension_mult: float = 1.0):
    """
    After the sweep/rejection bar, wait for price to move away from the
    level by at least min_extension_mult*buffer (confirming the rejection
    actually has legs), then find the first subsequent bar that pulls back
    to within `buffer` of the level again -- that's the retest entry.
    Returns the bar index of that retest touch, or None if it never happens
    within monitor_bars.
    """
    highs, lows = ltf["high"].values, ltf["low"].values
    moved_away = False
    best_ext = 0.0
    for i in range(sweep_idx + 1, min(sweep_idx + monitor_bars, len(ltf) - 1) + 1):
        if direction == "bullish":
            ext = highs[i] - level_price
            best_ext = max(best_ext, ext)
            if not moved_away and best_ext >= buffer * min_extension_mult:
                moved_away = True
            if moved_away and lows[i] <= level_price + buffer:
                return i
        else:
            ext = level_price - lows[i]
            best_ext = max(best_ext, ext)
            if not moved_away and best_ext >= buffer * min_extension_mult:
                moved_away = True
            if moved_away and highs[i] >= level_price - buffer:
                return i
    return None


def find_volume_breakout(ltf: pd.DataFrame, level_price: float, start_idx: int, end_idx: int,
                          vol_avg_window: int = 20, vol_mult: float = 1.5, body_frac: float = 0.6):
    """
    First bar in [start_idx, end_idx] that closes decisively beyond the
    level: a full-bodied candle (body >= body_frac of its own range, so not
    a thin wick/doji) on volume >= vol_mult times the trailing average, in
    either direction. Returns (direction, idx) where direction is the
    breakout direction (bullish = broke above, bearish = broke below), or
    None.
    """
    opens, highs, lows, closes = ltf["open"].values, ltf["high"].values, ltf["low"].values, ltf["close"].values
    volume = ltf["volume"].values
    lo = max(start_idx, vol_avg_window)
    for i in range(lo, min(end_idx, len(ltf) - 1) + 1):
        rng = highs[i] - lows[i]
        if rng <= 0:
            continue
        body = abs(closes[i] - opens[i])
        if body / rng < body_frac:
            continue
        avg_vol = volume[i - vol_avg_window:i].mean()
        if avg_vol <= 0 or volume[i] < vol_mult * avg_vol:
            continue
        if closes[i] > level_price and opens[i] <= level_price:
            return "bullish", i
        if closes[i] < level_price and opens[i] >= level_price:
            return "bearish", i
    return None


def run_sr_liquidity(symbol, htf_name="H4", htf_bars=9000, swing_window=3,
                      killzones=DEFAULT_KILLZONES, min_rr=2.0, max_rr=5.0,
                      max_hold_bars=200, monitor_htf_bars=120, level_buffer_frac=0.1,
                      mode="reversal", require_retest=False, retest_monitor_bars=200,
                      vol_avg_window=20, vol_mult=1.5, body_frac=0.6,
                      use_leg_filter=False, atr_period=14, min_leg_atr_mult=2.0,
                      breakeven_trigger_r=None, breakeven_buffer_r=0.0,
                      partial_r=None, partial_fraction=0.5, move_stop_to_be_after_partial=True,
                      use_m1_exit=False):
    """
    mode: "reversal" (sweep + fade the level) or "breakout" (full-bodied,
    above-average-volume close through the level -> trade the continuation).

    use_leg_filter: when True, levels come from build_leg_levels() instead of
    build_levels() -- only swings that represent a real leg (move >=
    min_leg_atr_mult x ATR from the prior kept swing) become S/R levels;
    smaller swings inside an ongoing leg are dropped as noise. This keeps
    only the previous major highs/lows live at any time, not every minor
    fractal pivot.
    """
    if mode not in ("reversal", "breakout"):
        raise ValueError("mode must be 'reversal' or 'breakout'")

    ltf_name = HTF_TO_LTF[htf_name]
    ltf_bar_minutes = {"H1": 60, "M15": 15, "M1": 1}[ltf_name]
    htf, ltf = fetch_htf_ltf(symbol, htf_name, ltf_name, htf_bars)
    if mode == "breakout":
        ltf = fetch_ltf_with_volume(symbol, ltf_name, ltf["time"].iloc[0], ltf["time"].iloc[-1])
    print(f"HTF {htf_name}: {len(htf)} bars, LTF {ltf_name}: {len(ltf)} bars")

    if use_leg_filter:
        levels = build_leg_levels(htf, swing_window, atr_period, min_leg_atr_mult)
    else:
        levels = build_levels(htf, swing_window)
    print(f"S/R levels: {len(levels)}")

    trades = []
    blocked_until = pd.Timestamp.min.tz_localize("UTC")

    for lvl in levels:
        if lvl["formed_idx"] >= len(htf):
            continue
        formed_time = htf["time"][lvl["formed_idx"]]
        if formed_time < blocked_until:
            continue
        monitor_end_htf_idx = min(lvl["formed_idx"] + monitor_htf_bars, len(htf) - 1)
        monitor_end_time = htf["time"][monitor_end_htf_idx]

        ltf_start_idx = ltf.index[ltf["time"] >= formed_time]
        ltf_end_idx = ltf.index[ltf["time"] <= monitor_end_time]
        if len(ltf_start_idx) == 0 or len(ltf_end_idx) == 0:
            continue
        start_i, end_i = ltf_start_idx[0], ltf_end_idx[-1]
        if end_i <= start_i:
            continue

        if mode == "reversal":
            result = find_level_sweep(ltf, lvl["price"], start_i, end_i)
            if result is None:
                continue
            direction, trigger_idx = result

            # sweep wick extreme on the correct side: bullish = support rejection (wick below),
            # bearish = resistance rejection (wick above)
            sweep_extreme = ltf["low"][trigger_idx] if direction == "bullish" else ltf["high"][trigger_idx]
            buffer = abs(sweep_extreme - lvl["price"]) * level_buffer_frac + abs(lvl["price"]) * 0.0002

            if require_retest:
                retest_idx = find_retest_entry(ltf, lvl["price"], trigger_idx, direction, buffer,
                                                monitor_bars=retest_monitor_bars)
                if retest_idx is None:
                    continue
                entry_idx = retest_idx + 1
            else:
                entry_idx = trigger_idx + 1
        else:  # breakout
            result = find_volume_breakout(ltf, lvl["price"], start_i, end_i,
                                           vol_avg_window=vol_avg_window, vol_mult=vol_mult, body_frac=body_frac)
            if result is None:
                continue
            direction, trigger_idx = result
            buffer = abs(lvl["price"]) * 0.0005
            entry_idx = trigger_idx + 1

        if entry_idx >= len(ltf):
            continue
        entry_time = ltf["time"][entry_idx]
        if not in_killzone(entry_time, killzones):
            continue
        entry_price = ltf["open"][entry_idx]

        if mode == "reversal":
            if direction == "bullish":
                stop = min(sweep_extreme, lvl["price"]) - buffer
                risk = entry_price - stop
            else:
                stop = max(sweep_extreme, lvl["price"]) + buffer
                risk = stop - entry_price
        else:  # breakout: invalidated if price falls back inside the broken level
            if direction == "bullish":
                stop = lvl["price"] - buffer
                risk = entry_price - stop
            else:
                stop = lvl["price"] + buffer
                risk = stop - entry_price
        if risk <= 0:
            continue

        other_levels = [l["price"] for l in levels if l["formed_idx"] <= lvl["formed_idx"]]
        if direction == "bullish":
            candidates = [p for p in other_levels if p > entry_price]
            dist = min((p - entry_price) for p in candidates) if candidates else None
        else:
            candidates = [p for p in other_levels if p < entry_price]
            dist = min((entry_price - p) for p in candidates) if candidates else None

        if dist is not None and min_rr <= dist / risk:
            rr_target = min(dist / risk, max_rr)
        else:
            rr_target = max_rr
        target_price = entry_price + rr_target * risk if direction == "bullish" else entry_price - rr_target * risk

        partial_taken = None
        if use_m1_exit:
            outcome, r, exit_time, partial_taken = simulate_exit_m1(
                symbol, ltf, entry_idx, entry_price, stop, target_price, rr_target, direction,
                max_hold_bars, risk, ltf_bar_minutes,
                breakeven_trigger_r=breakeven_trigger_r, breakeven_buffer_r=breakeven_buffer_r,
                partial_r=partial_r, partial_fraction=partial_fraction,
                move_stop_to_be_after_partial=move_stop_to_be_after_partial)
            exit_price = entry_price + r * risk * (1 if direction == "bullish" else -1)
        elif partial_r is not None:
            outcome, r, exit_idx, partial_taken = simulate_exit_partial(
                ltf, entry_idx, entry_price, stop, target_price, rr_target, direction, max_hold_bars, risk,
                partial_r, partial_fraction, move_stop_to_be_after_partial)
            exit_time = ltf["time"][exit_idx]
            exit_price = entry_price + r * risk * (1 if direction == "bullish" else -1)
        else:
            outcome, exit_price, exit_idx, r = simulate_exit(
                ltf, entry_idx, entry_price, stop, target_price, rr_target, direction, max_hold_bars, risk,
                breakeven_trigger_r=breakeven_trigger_r, breakeven_buffer_r=breakeven_buffer_r)
            exit_time = ltf["time"][exit_idx]

        trades.append({
            "level_price": lvl["price"], "level_kind": lvl["kind"], "level_formed_time": formed_time,
            "direction": "long" if direction == "bullish" else "short",
            "mode": mode, "trigger_time": ltf["time"][trigger_idx],
            "entry_time": ltf["time"][entry_idx], "exit_time": exit_time,
            "entry": entry_price, "stop": stop, "target": target_price,
            "exit_price": exit_price, "outcome": outcome, "r_multiple": r,
            "rr_planned": rr_target, "partial_taken": partial_taken,
        })
        blocked_until = exit_time

    return pd.DataFrame(trades)


def main():
    ap = argparse.ArgumentParser(description="Support/Resistance + Liquidity backtest")
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--htf", default="H4", choices=HTF_TO_LTF.keys())
    ap.add_argument("--htf-bars", type=int, default=9000)
    ap.add_argument("--swing-window", type=int, default=3)
    ap.add_argument("--min-rr", type=float, default=2.0)
    ap.add_argument("--max-rr", type=float, default=5.0)
    ap.add_argument("--mode", default="reversal", choices=["reversal", "breakout"])
    ap.add_argument("--require-retest", action="store_true")
    ap.add_argument("--vol-mult", type=float, default=1.5)
    ap.add_argument("--body-frac", type=float, default=0.6)
    ap.add_argument("--use-leg-filter", action="store_true")
    ap.add_argument("--min-leg-atr-mult", type=float, default=2.0)
    ap.add_argument("--breakeven-trigger-r", type=float, default=None)
    ap.add_argument("--breakeven-buffer-r", type=float, default=0.0)
    ap.add_argument("--partial-r", type=float, default=None)
    ap.add_argument("--partial-fraction", type=float, default=0.5)
    ap.add_argument("--no-be-after-partial", action="store_true")
    ap.add_argument("--use-m1-exit", action="store_true")
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--out", default="sr_liquidity_trades.csv")
    args = ap.parse_args()

    trades = run_sr_liquidity(args.symbol, args.htf, args.htf_bars, args.swing_window,
                               min_rr=args.min_rr, max_rr=args.max_rr, mode=args.mode,
                               require_retest=args.require_retest, vol_mult=args.vol_mult,
                               body_frac=args.body_frac, use_leg_filter=args.use_leg_filter,
                               min_leg_atr_mult=args.min_leg_atr_mult,
                               breakeven_trigger_r=args.breakeven_trigger_r,
                               breakeven_buffer_r=args.breakeven_buffer_r,
                               partial_r=args.partial_r, partial_fraction=args.partial_fraction,
                               move_stop_to_be_after_partial=not args.no_be_after_partial,
                               use_m1_exit=args.use_m1_exit)
    if not trades.empty:
        trades.to_csv(args.out, index=False)
        print(f"Saved {len(trades)} trades to {args.out}")
    summarize(trades, risk_pct=args.risk_pct)


if __name__ == "__main__":
    main()
