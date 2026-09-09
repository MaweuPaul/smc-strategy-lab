"""
Daily Order Block retracement backtest.

  1. On D1, detect swing highs/lows and Order Blocks (last opposite-colored
     candle before a break-of-structure impulse) -- reusing find_order_blocks
     from smc_backtest.py. A bullish OB = last down (black) candle right
     before an up-move that broke the prior swing high. A bearish OB = last
     up (green) candle right before a down-move that broke the prior swing low.
  2. Once an OB is confirmed, watch H1 for the first bar whose range touches
     back into the OB's candle range (retracement into the zone).
  3. Enter in the ORIGINAL impulse direction (continuation, not a fade):
     bullish OB touched -> long; bearish OB touched -> short.
  4. Stop beyond the OB zone. Target = the next opposing OB zone on the
     correct side of price (RR-clipped), falling back to a flat RR.

One trade per OB. Data is read-only from a running MetaTrader5 terminal; no
orders are ever placed.
"""


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # repo root, for shared modules (smc_backtest, etc.)

import argparse
from datetime import timedelta

import pandas as pd
import MetaTrader5 as mt5

from smc_backtest import find_swings, find_order_blocks, find_fvgs, structure_bias_series, TIMEFRAMES
from htf_ltf_backtest import (
    DEFAULT_KILLZONES, fetch_htf_ltf, in_killzone,
)
from daily_ob_support import completed_bars, simulate_daily_exit, account_trades


def fetch_ltf_custom(symbol: str, ltf_name: str, start, end) -> pd.DataFrame:
    rates = mt5.copy_rates_range(symbol, TIMEFRAMES[ltf_name], start, end)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No {ltf_name} data: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[["time", "open", "high", "low", "close"]].reset_index(drop=True)


def find_choch_entry(window: pd.DataFrame, touch_idx: int, direction: str,
                      minor_window: int = 1, lookback_for_swing: int = 40, monitor_bars: int = 100):
    """
    After price touches the OB zone, find the minor swing point formed
    during the pullback (a lower high for a bullish continuation, a higher
    low for bearish) and wait for a subsequent close beyond it -- a
    Change of Character (CHoCH) confirming the original direction has
    resumed. Returns (choch_idx, stop_reference_price) in `window`'s local
    index space, or None. stop_reference is the extreme reached between the
    touch and the CHoCH -- tighter than the full daily OB zone.
    """
    start = max(0, touch_idx - lookback_for_swing)
    end = min(touch_idx + monitor_bars, len(window) - 1)
    sub = window.iloc[start:end + 1].reset_index(drop=True)
    local_touch = touch_idx - start
    minor_highs, minor_lows = find_swings(sub, window=minor_window)
    closes = sub["close"].values

    if direction == "bullish":
        prior_highs = [h for h in minor_highs if h + minor_window <= local_touch]
        if not prior_highs:
            return None
        struct_high = sub["high"].values[prior_highs[-1]]
        for j in range(local_touch + 1, len(sub)):
            if closes[j] > struct_high:
                stop_ref = sub["low"].values[local_touch:j + 1].min()
                return start + j, stop_ref
    else:
        prior_lows = [l for l in minor_lows if l + minor_window <= local_touch]
        if not prior_lows:
            return None
        struct_low = sub["low"].values[prior_lows[-1]]
        for j in range(local_touch + 1, len(sub)):
            if closes[j] < struct_low:
                stop_ref = sub["high"].values[local_touch:j + 1].max()
                return start + j, stop_ref
    return None


def find_pyramid_trigger(ltf: pd.DataFrame, entry_idx: int, base_exit_idx: int,
                          entry_price: float, risk: float, direction: str, trigger_r: float):
    """First bar in [entry_idx, base_exit_idx) where the base trade reaches
    trigger_r in favor. Returns that bar's index, or None if it never gets
    there before the base trade's own exit."""
    for k in range(entry_idx, base_exit_idx):
        if direction == "bullish":
            if ltf["high"][k] >= entry_price + trigger_r * risk:
                return k
        else:
            if ltf["low"][k] <= entry_price - trigger_r * risk:
                return k
    return None


def find_invalidation_exit(ltf: pd.DataFrame, entry_idx: int, search_end_idx: int, direction: str,
                            minor_window: int = 1, lookback: int = 20,
                            confirm_bars: int = 1, grace_bars: int = 0):
    """
    After entry, watch for a structure break AGAINST the trade: a close
    beyond the most recent minor swing point that formed at/before entry
    (on the entry timeframe). If that happens before the trade's normal
    stop or target would resolve it, the setup is invalidated -- cut it
    there instead of riding to the full stop. Returns the bar index of the
    invalidating close, or None if it never happens within search_end_idx.

    minor_window: bigger = only more significant swings count as the
    structure reference (less noise-sensitive).
    confirm_bars: require this many CONSECUTIVE closes beyond the level
    before calling it invalidated (1 = a single close is enough).
    grace_bars: don't check for invalidation until this many bars after
    entry, so normal early noise can't trigger it immediately.
    """
    if minor_window < 1 or confirm_bars < 1 or grace_bars < 0 or lookback < 1:
        raise ValueError("Invalid invalidation parameters")
    start = max(0, entry_idx - lookback)
    sub = ltf.iloc[start:search_end_idx + 1].reset_index(drop=True)
    local_entry = entry_idx - start
    if local_entry < 0 or local_entry >= len(sub):
        return None
    minor_highs, minor_lows = find_swings(sub, window=minor_window)
    closes = sub["close"].values
    check_from = local_entry + 1 + grace_bars

    def confirmed_break(j, beyond_fn):
        if j + confirm_bars > len(sub):
            return False
        return all(beyond_fn(closes[j + k]) for k in range(confirm_bars))

    if direction == "bullish":
        prior_lows = [l for l in minor_lows if l + minor_window < local_entry]
        if not prior_lows:
            return None
        struct_low = sub["low"].values[prior_lows[-1]]
        for j in range(check_from, len(sub)):
            if confirmed_break(j, lambda c: c < struct_low):
                return start + j + confirm_bars - 1
    else:
        prior_highs = [h for h in minor_highs if h + minor_window < local_entry]
        if not prior_highs:
            return None
        struct_high = sub["high"].values[prior_highs[-1]]
        for j in range(check_from, len(sub)):
            if confirmed_break(j, lambda c: c > struct_high):
                return start + j + confirm_bars - 1
    return None


def bias_at_time(htf: pd.DataFrame, bias_arr, t) -> str:
    idx = htf.index[htf["close_time"] <= t]
    if len(idx) == 0:
        return "neutral"
    return bias_arr[idx[-1]]


def filter_obs_by_displacement(obs, htf, fvg_window=3):
    """Keep qualifying OBs, timestamped when displacement first becomes known."""
    fvgs = find_fvgs(htf)
    kept = []
    for ob in obs:
        matches = [g for g in fvgs if g["type"] == ob["type"]
                   and abs(g["formed_at"] - ob["formed_at"]) <= fvg_window]
        if matches:
            gap = min(matches, key=lambda g: g["formed_at"])
            kept.append({**ob, "qualified_at": max(ob["formed_at"], gap["formed_at"]),
                         "fvg_formed_at": gap["formed_at"],
                         "fvg_top": gap["top"], "fvg_bottom": gap["bottom"]})
    return kept


def run_daily_ob(symbol, htf_bars=9000, swing_window=2, ob_lookback=6,
                  killzones=None, min_rr=2.0, max_rr=5.0,
                  max_hold_bars=400, monitor_days=60, zone_buffer_frac=0.1,
                  entry_precision="h1", choch_minor_window=1, bias_mode="off",
                  require_displacement=False, displacement_fvg_window=3,
                  pyramid_trigger_r=None, pyramid_risk_mult=0.5,
                  use_invalidation_exit=False, invalidation_lookback=20,
                  invalidation_minor_window=1, invalidation_confirm_bars=1, invalidation_grace_bars=0,
                  htf_name="D1", ltf_name="H1", skip_weekdays=None,
                  breakeven_trigger_r=None, breakeven_buffer_r=0.0,
                  *, htf_data=None, ltf_data=None, as_of=None):
    """
    pyramid_trigger_r: if set, once the base trade reaches this many R in
    favor, add a second leg at market. The add's stop sits at the base
    trade's entry price (breakeven of the base), and it aims at the same
    target as the base -- riding the same thesis with a tighter, already-
    de-risked add. Sized at pyramid_risk_mult of normal risk. None = off.

    killzones=None means no time-of-day filter (this is a daily-swing
    strategy, not a session-timing one) -- pass a killzone list to restrict
    entries to specific UTC hours if you want to.

    entry_precision: "h1" (default) enters on the first H1 bar that touches
    the OB zone, stop beyond the zone. "m15_choch" instead watches M15 for a
    Change of Character after the touch and enters then, with the stop
    placed at the retracement's actual extreme (tighter than the full
    daily zone) instead of the zone boundary.

    bias_mode: "off" (default) always trades the OB's original impulse
    direction. "filter" skips a trade whose direction disagrees with the D1
    structure bias (HH+HL/LH+LL) as of the retracement touch. "reverse"
    instead flips to the opposite direction on disagreement -- fading the
    zone as a "breaker block" (an old demand/supply zone that flips role
    once the higher-timeframe trend has turned).
    """
    if (swing_window < 1 or ob_lookback < 1 or max_hold_bars < 1 or monitor_days < 1
            or choch_minor_window < 1 or invalidation_minor_window < 1
            or invalidation_confirm_bars < 1 or invalidation_grace_bars < 0
            or invalidation_lookback < 1 or displacement_fvg_window < 0
            or zone_buffer_frac < 0 or not 0 < min_rr <= max_rr or pyramid_risk_mult <= 0):
        raise ValueError("Invalid strategy windows, risk or RR bounds")
    if bias_mode not in ("off", "filter", "reverse") or entry_precision not in ("h1", "m15_choch"):
        raise ValueError("Unsupported bias or entry mode")
    if pyramid_trigger_r is not None and pyramid_trigger_r <= 0:
        raise ValueError("Pyramid trigger must be positive")
    entry_ltf_name = "M15" if entry_precision == "m15_choch" else ltf_name
    if (htf_data is None) != (ltf_data is None):
        raise ValueError("Provide both HTF and LTF data, or neither")
    htf, ltf = fetch_htf_ltf(symbol, htf_name, ltf_name, htf_bars) if htf_data is None else (htf_data, ltf_data)
    if entry_precision == "m15_choch" and ltf_data is None:
        ltf = fetch_ltf_custom(symbol, "M15", htf.time.iloc[0] - timedelta(days=2),
                               htf.time.iloc[-1] + timedelta(days=2))
    cutoff = pd.Timestamp.now(tz="UTC") if as_of is None else as_of
    htf = completed_bars(htf, htf_name, cutoff)
    ltf = completed_bars(ltf, entry_ltf_name, cutoff)
    if htf.empty or ltf.empty:
        return pd.DataFrame()
    coverage = {"evaluation_start": max(htf.time.iloc[0], ltf.time.iloc[0]),
                "evaluation_end": ltf.close_time.iloc[-1], "ltf": entry_ltf_name}
    print(f"{htf_name}: {len(htf)} closed bars, {entry_ltf_name}: {len(ltf)} closed bars")

    swing_highs, swing_lows = find_swings(htf, window=swing_window)
    obs = find_order_blocks(htf, swing_highs, swing_lows, lookback=ob_lookback, swing_window=swing_window)
    if require_displacement:
        obs = filter_obs_by_displacement(obs, htf, fvg_window=displacement_fvg_window)
    for ob in obs:
        ob["confirmed_time"] = htf.close_time.iloc[ob["formed_at"]]
        ob["available_time"] = htf.close_time.iloc[ob.get("qualified_at", ob["formed_at"])]
    obs.sort(key=lambda o: o["formed_at"])
    print(f"Order blocks: {len(obs)}")

    bias_arr = structure_bias_series(htf, swing_highs, swing_lows, swing_window=swing_window) if bias_mode != "off" else None

    # Phase 1: independently work out what trade (if any) each OB would
    # produce, with no cross-OB blocking applied yet. Each OB's outcome only
    # depends on its own zone and the price path after it -- never on
    # whether some other OB's trade is "in the way" -- so this can be done
    # for every OB up front, and only THEN do we decide which of these
    # (possibly overlapping) candidates actually get taken.
    candidates = []
    for ob in obs:
        # The breakout is only confirmed once its own bar CLOSES -- formed_at
        # is that bar's index, so the earliest a live trader could know about
        # this OB is the open of the *next* bar, not formed_at's own open.
        formed_time = htf["time"][ob["formed_at"]]
        confirmed_time = ob["available_time"]
        window_end_htf_idx = min(ob["formed_at"] + monitor_days, len(htf) - 1)
        window_end_time = htf["close_time"][window_end_htf_idx]

        ltf_mask = (ltf["time"] >= confirmed_time) & (ltf["close_time"] <= window_end_time)
        window = ltf.loc[ltf_mask].reset_index(drop=True)
        if window.empty:
            continue

        touch_idx = None
        for i in range(len(window)):
            if window["low"][i] <= ob["top"] and window["high"][i] >= ob["bottom"]:
                if skip_weekdays and window["time"][i].day_name() in skip_weekdays:
                    continue  # thin weekly-reopen bar -- keep watching for a real touch
                touch_idx = i
                break
        if touch_idx is None:
            continue

        direction = ob["type"]
        touch_time = window["time"][touch_idx]

        if bias_mode != "off":
            cur_bias = bias_at_time(htf, bias_arr, window.close_time.iloc[touch_idx])
            mismatch = (direction == "bullish" and cur_bias == "bearish") or \
                       (direction == "bearish" and cur_bias == "bullish")
            if mismatch:
                if bias_mode == "filter":
                    continue
                elif bias_mode == "reverse":
                    direction = "bearish" if direction == "bullish" else "bullish"

        if entry_precision == "m15_choch":
            choch_result = find_choch_entry(window, touch_idx, direction, minor_window=choch_minor_window)
            if choch_result is None:
                continue
            choch_idx, stop_ref = choch_result
            signal_time = window["time"][choch_idx]
        else:
            signal_time = touch_time

        gi = ltf.index[ltf["time"] == signal_time]
        if len(gi) == 0:
            continue
        gi = gi[0]
        entry_idx = gi + 1
        if entry_idx >= len(ltf):
            continue
        if killzones is not None and not in_killzone(ltf.time.iloc[entry_idx], killzones):
            continue
        entry_price = ltf["open"][entry_idx]

        buffer = (ob["top"] - ob["bottom"]) * zone_buffer_frac
        if entry_precision == "m15_choch":
            if direction == "bullish":
                stop = stop_ref - buffer
                risk = entry_price - stop
            else:
                stop = stop_ref + buffer
                risk = stop - entry_price
        else:
            if direction == "bullish":
                stop = ob["bottom"] - buffer
                risk = entry_price - stop
            else:
                stop = ob["top"] + buffer
                risk = stop - entry_price
        if risk <= 0:
            continue

        prior_obs = [o for o in obs if o["available_time"] <= ltf.close_time.iloc[gi]]
        if direction == "bullish":
            opposing = [o["bottom"] for o in prior_obs if o["type"] == "bearish" and o["bottom"] > entry_price]
            dist = min((p - entry_price) for p in opposing) if opposing else None
        else:
            opposing = [o["top"] for o in prior_obs if o["type"] == "bullish" and o["top"] < entry_price]
            dist = min((entry_price - p) for p in opposing) if opposing else None

        if dist is not None and min_rr <= dist / risk:
            rr_target = min(dist / risk, max_rr)
        else:
            rr_target = max_rr
        target_price = entry_price + rr_target * risk if direction == "bullish" else entry_price - rr_target * risk

        invalidation_idx = None
        if use_invalidation_exit:
            invalidation_idx = find_invalidation_exit(
                ltf, entry_idx, min(entry_idx + max_hold_bars - 1, len(ltf) - 1), direction,
                minor_window=invalidation_minor_window, lookback=invalidation_lookback,
                confirm_bars=invalidation_confirm_bars, grace_bars=invalidation_grace_bars)
        fill = simulate_daily_exit(
            ltf, entry_idx, entry_price, stop, target_price, direction, max_hold_bars, risk,
            invalidation_idx, breakeven_trigger_r, breakeven_buffer_r)
        exit_idx = fill["exit_idx"]
        chain_id = f"{symbol}:{formed_time.isoformat()}:{ob['type']}"
        common = {
            "symbol": symbol, "chain_id": chain_id, "ob_type": ob["type"],
            "ob_top": ob["top"], "ob_bottom": ob["bottom"],
            "ob_formed_time": formed_time, "ob_confirmed_time": ob["confirmed_time"],
            "ob_available_time": ob["available_time"],
            "ob_candle_time": htf.time.iloc[ob["candle_idx"]],
            "fvg_confirmed_time": htf.close_time.iloc[ob["fvg_formed_at"]] if "fvg_formed_at" in ob else None,
            "fvg_top": ob.get("fvg_top"), "fvg_bottom": ob.get("fvg_bottom"),
            "direction": "long" if direction == "bullish" else "short",
            "touch_time": touch_time, "touch_confirmed_time": window.close_time.iloc[touch_idx],
            "signal_time": signal_time, "signal_confirmed_time": ltf.close_time.iloc[gi],
            "ltf": entry_ltf_name, "htf": htf_name,
            "replay_end_time": ltf.time.iloc[entry_idx] + timedelta(minutes=max_hold_bars *
                (15 if entry_ltf_name == "M15" else 60)),
        }
        def record_fill(fill_data):
            event = invalidation_idx
            return {**fill_data, "invalidation_signal_time":
                    ltf.close_time.iloc[event] if event is not None and event < fill_data["exit_idx"] else None}
        base_trade = {**common, **record_fill(fill), "entry_time": ltf.time.iloc[entry_idx],
                      "entry": entry_price, "stop": stop, "target": target_price,
                      "rr_planned": rr_target, "leg_type": "base", "risk_mult": 1.0}
        chain_end_time = fill["exit_time"]
        pyramid_trade = None
        if pyramid_trigger_r is not None:
            trig_idx = find_pyramid_trigger(ltf, entry_idx, exit_idx, entry_price, risk,
                                            direction, pyramid_trigger_r)
            if trig_idx is not None:
                add_entry_idx = trig_idx + 1
                # Cancel if the base exits at this open; an intrabar exit later
                # on the add's entry candle is not knowable at its open.
                alive = add_entry_idx < exit_idx or (add_entry_idx == exit_idx and fill["exit_phase"] != "open")
                permitted = killzones is None or in_killzone(ltf.time.iloc[add_entry_idx], killzones)
                if alive and permitted:
                    add_entry = ltf.open.iloc[add_entry_idx]
                    add_stop = entry_price
                    sign = 1 if direction == "bullish" else -1
                    add_risk = sign * (add_entry - add_stop)
                    if add_risk > 0 and sign * (target_price - add_entry) > 0:
                        add_rr = sign * (target_price - add_entry) / add_risk
                        # The base structure invalidation applies to the entire OB chain.
                        add_fill = simulate_daily_exit(ltf, add_entry_idx, add_entry, add_stop,
                            target_price, direction, max_hold_bars, add_risk, invalidation_idx)
                        pyramid_trade = {**common, **record_fill(add_fill),
                            "entry_time": ltf.time.iloc[add_entry_idx], "entry": add_entry,
                            "stop": add_stop, "target": target_price, "rr_planned": add_rr,
                            "signal_time": ltf.time.iloc[trig_idx],
                            "signal_confirmed_time": ltf.close_time.iloc[trig_idx],
                            "leg_type": "pyramid", "risk_mult": pyramid_risk_mult}
                        chain_end_time = max(chain_end_time, add_fill["exit_time"])

        candidates.append({
            "entry_time": base_trade["entry_time"], "chain_end_time": chain_end_time,
            "base_trade": base_trade, "pyramid_trade": pyramid_trade,
        })

    # Phase 2: candidates can overlap in real time (a later-formed OB can be
    # touched before an earlier-formed one). Selecting strictly by actual
    # entry-time order -- not formation order -- is what makes "one trade
    # at a time" correct: whichever setup would genuinely trigger first
    # chronologically wins the slot, and blocks anything overlapping it.
    candidates.sort(key=lambda c: c["entry_time"])
    trades = []
    blocked_until = pd.Timestamp.min.tz_localize("UTC")
    for cand in candidates:
        if cand["entry_time"] < blocked_until:
            continue
        trades.append(cand["base_trade"])
        if cand["pyramid_trade"] is not None:
            trades.append(cand["pyramid_trade"])
        blocked_until = cand["chain_end_time"]

    result = pd.DataFrame(trades)
    result.attrs.update(coverage)
    return result


def main():
    ap = argparse.ArgumentParser(description="Daily Order Block retracement backtest")
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--htf-bars", type=int, default=9000)
    ap.add_argument("--swing-window", type=int, default=2)
    ap.add_argument("--min-rr", type=float, default=2.0)
    ap.add_argument("--max-rr", type=float, default=5.0)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--use-killzones", action="store_true")
    ap.add_argument("--entry-precision", default="h1", choices=["h1", "m15_choch"])
    ap.add_argument("--bias-mode", default="off", choices=["off", "filter", "reverse"])
    ap.add_argument("--pyramid-trigger-r", type=float, default=None)
    ap.add_argument("--pyramid-risk-mult", type=float, default=0.5)
    ap.add_argument("--out", default="daily_ob_trades.csv")
    args = ap.parse_args()

    kz = DEFAULT_KILLZONES if args.use_killzones else None
    trades = run_daily_ob(args.symbol, args.htf_bars, args.swing_window,
                           killzones=kz, min_rr=args.min_rr, max_rr=args.max_rr,
                           entry_precision=args.entry_precision, bias_mode=args.bias_mode,
                           pyramid_trigger_r=args.pyramid_trigger_r, pyramid_risk_mult=args.pyramid_risk_mult)
    if not trades.empty:
        trades.to_csv(args.out, index=False)
        print(f"Saved {len(trades)} trades to {args.out}")
    if not trades.empty:
        accounted, metrics = account_trades(trades, args.risk_pct, 10_000)
        print(f"Realized balance: {metrics['final_equity']:.2f}; realized drawdown: {metrics['max_drawdown_pct']:.2f}%")


if __name__ == "__main__":
    main()
