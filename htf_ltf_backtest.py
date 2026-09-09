"""
HTF-bias -> LTF-confirmation ICT backtest.

Process implemented (per the trading process supplied):
  1. HTF key level / Point of Power (POI) = an OB/FVG/IFVG zone on the HTF.
  2. When HTF price trades into a POI, open an LTF confirmation window.
  3. Inside that window on the LTF: look for a liquidity sweep (TS) of a minor
     swing in the POI's direction.
  4. After the sweep, require a Market Structure Shift / CISD on the LTF
     (a close beyond the most recent opposing internal swing) -- this is
     treated as one confirmation test in this implementation.
  5. After the shift, wait for a pullback into the OTE (61.8%-79% fib) of the
     impulse leg created by the shift; refine the entry against any LTF
     FVG/OB found inside that OTE zone.
  6. Target = nearest opposing HTF zone, clipped to a 3R-5R range; if no
     opposing zone qualifies, use a flat 4R target. Stop = beyond the sweep
     extreme.
  7. No confirmation within the window -> no trade.
  8. Entries are filtered to the London / New York killzones (UTC hours,
     configurable).
  9. Optional pyramiding: after the base entry, watch the LTF for a break of
     structure that leaves behind a fresh same-direction FVG (the broken
     level is the liquidity left behind; the FVG next to it is the zone).
     When price retraces into that zone, add a reduced-risk leg. Repeats up
     to a configurable cap.

Simplification note: SMT divergence (step 3's alternative confirmation) is
NOT implemented -- it requires a second correlated symbol evaluated in lock
step and is left as a possible extension. MSS and CISD are treated as the
same structural test here rather than two independently modeled signals.

Data comes read-only from a running MetaTrader5 terminal; no orders are ever
placed.
"""

import argparse
from datetime import timedelta

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from smc_backtest import (
    TIMEFRAMES, find_swings, find_fvgs, update_fvg_status,
    find_order_blocks, build_zones, structure_bias_series,
)

HTF_TO_LTF = {"D1": "H1", "H4": "M15", "M15": "M1"}

DEFAULT_KILLZONES = [(7, 10), (12, 15)]  # UTC hours: London, New York


def in_killzone(ts: pd.Timestamp, killzones) -> bool:
    h = ts.hour
    return any(start <= h < end for start, end in killzones)


def fetch_htf_ltf(symbol: str, htf_name: str, ltf_name: str, htf_bars: int):
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
    if mt5.symbol_info(symbol) is None:
        raise RuntimeError(f"Symbol {symbol} not found")

    htf_rates = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[htf_name], 0, htf_bars)
    if htf_rates is None or len(htf_rates) == 0:
        raise RuntimeError(f"No HTF data: {mt5.last_error()}")
    htf = pd.DataFrame(htf_rates)
    htf["time"] = pd.to_datetime(htf["time"], unit="s", utc=True)
    htf = htf[["time", "open", "high", "low", "close"]].reset_index(drop=True)

    start = htf["time"].iloc[0] - timedelta(days=2)
    end = htf["time"].iloc[-1] + timedelta(days=2)
    ltf_rates = mt5.copy_rates_range(symbol, TIMEFRAMES[ltf_name], start, end)
    if ltf_rates is None or len(ltf_rates) == 0:
        raise RuntimeError(f"No LTF data: {mt5.last_error()}")
    ltf = pd.DataFrame(ltf_rates)
    ltf["time"] = pd.to_datetime(ltf["time"], unit="s", utc=True)
    ltf = ltf[["time", "open", "high", "low", "close"]].reset_index(drop=True)

    return htf, ltf


def build_htf_zones(htf: pd.DataFrame):
    swing_highs, swing_lows = find_swings(htf, window=2)
    fvgs = find_fvgs(htf)
    update_fvg_status(fvgs, htf, len(htf) - 1)
    obs = find_order_blocks(htf, swing_highs, swing_lows, swing_window=2)
    zones = build_zones(obs, fvgs)
    return zones


def poi_touch_time(htf: pd.DataFrame, zone: dict):
    """First HTF bar after formation where price trades into the zone."""
    for i in range(zone["formed_at"] + 1, len(htf)):
        if htf["low"][i] <= zone["top"] and htf["high"][i] >= zone["bottom"]:
            return i
    return None


def find_confirmation(ltf_win: pd.DataFrame, direction: str, minor_window=1):
    """
    Look for sweep -> MSS/CISD -> OTE pullback inside one LTF window.
    Returns a dict describing the setup, or None.
    """
    if len(ltf_win) < 6:
        return None
    minor_highs, minor_lows = find_swings(ltf_win, window=minor_window)
    highs, lows, closes = ltf_win["high"].values, ltf_win["low"].values, ltf_win["close"].values

    if direction == "bullish":
        for s in minor_lows:
            sweep_low = lows[s]
            for j in range(s + 1, len(ltf_win)):
                if lows[j] < sweep_low and closes[j] > sweep_low:
                    sweep_idx, sweep_extreme = j, lows[j]
                    prior_highs = [h for h in minor_highs if h + minor_window <= s]
                    if not prior_highs:
                        continue
                    struct_high = highs[prior_highs[-1]]
                    for m in range(sweep_idx + 1, len(ltf_win)):
                        if closes[m] > struct_high:
                            leg_high = highs[sweep_idx:m + 1].max()
                            leg_low = sweep_extreme
                            span = leg_high - leg_low
                            if span <= 0:
                                continue
                            ote_top = leg_high - 0.618 * span
                            ote_bottom = leg_high - 0.79 * span
                            for e in range(m + 1, len(ltf_win)):
                                if ote_bottom <= lows[e] <= ote_top or ote_bottom <= highs[e] <= ote_top:
                                    return {
                                        "sweep_idx": sweep_idx, "mss_idx": m, "entry_idx": e,
                                        "sweep_extreme": sweep_extreme,
                                        "ote_top": ote_top, "ote_bottom": ote_bottom,
                                    }
                    break  # only test the first sweep of this swing
    else:
        for s in minor_highs:
            sweep_high = highs[s]
            for j in range(s + 1, len(ltf_win)):
                if highs[j] > sweep_high and closes[j] < sweep_high:
                    sweep_idx, sweep_extreme = j, highs[j]
                    prior_lows = [l for l in minor_lows if l + minor_window <= s]
                    if not prior_lows:
                        continue
                    struct_low = lows[prior_lows[-1]]
                    for m in range(sweep_idx + 1, len(ltf_win)):
                        if closes[m] < struct_low:
                            leg_low = lows[sweep_idx:m + 1].min()
                            leg_high = sweep_extreme
                            span = leg_high - leg_low
                            if span <= 0:
                                continue
                            ote_bottom = leg_low + 0.618 * span
                            ote_top = leg_low + 0.79 * span
                            for e in range(m + 1, len(ltf_win)):
                                if ote_bottom <= lows[e] <= ote_top or ote_bottom <= highs[e] <= ote_top:
                                    return {
                                        "sweep_idx": sweep_idx, "mss_idx": m, "entry_idx": e,
                                        "sweep_extreme": sweep_extreme,
                                        "ote_top": ote_top, "ote_bottom": ote_bottom,
                                    }
                    break
    return None


def resolve_target(zones, direction, entry_price, risk, min_rr, max_rr):
    if direction == "bullish":
        opposing = [z for z in zones if z["type"] == "bearish" and z["bottom"] > entry_price]
        dist = min((z["bottom"] - entry_price for z in opposing), default=None)
    else:
        opposing = [z for z in zones if z["type"] == "bullish" and z["top"] < entry_price]
        dist = min((entry_price - z["top"] for z in opposing), default=None)

    if dist is not None and min_rr <= dist / risk:
        rr_target = min(dist / risk, max_rr)
    else:
        rr_target = max_rr
    target_price = entry_price + rr_target * risk if direction == "bullish" else entry_price - rr_target * risk
    return target_price, rr_target


def simulate_exit(ltf, entry_idx, entry_price, stop, target_price, rr_target, direction, max_hold_ltf_bars, risk,
                   breakeven_trigger_r=None, breakeven_buffer_r=0.0):
    """
    breakeven_trigger_r: once price moves this many R in favor, move the
    stop to entry_price + breakeven_buffer_r*risk (breakeven, or a small
    locked-in profit/loss if the buffer is nonzero). None = no breakeven
    management (original behavior). Within a bar, the favorable extreme is
    checked before the stop -- a standard, if imperfect, OHLC-only
    approximation of intrabar order.
    """
    outcome, exit_price, exit_idx = None, None, None
    end = min(entry_idx + max_hold_ltf_bars, len(ltf) - 1)
    current_stop = stop
    be_armed = False
    be_price = entry_price + breakeven_buffer_r * risk if direction == "bullish" else entry_price - breakeven_buffer_r * risk

    for k in range(entry_idx, end + 1):
        if direction == "bullish":
            if breakeven_trigger_r is not None and not be_armed:
                if ltf["high"][k] >= entry_price + breakeven_trigger_r * risk:
                    be_armed = True
                    current_stop = max(current_stop, be_price)
            if ltf["low"][k] <= current_stop:
                outcome, exit_price, exit_idx = "loss", current_stop, k
                break
            if ltf["high"][k] >= target_price:
                outcome, exit_price, exit_idx = "win", target_price, k
                break
        else:
            if breakeven_trigger_r is not None and not be_armed:
                if ltf["low"][k] <= entry_price - breakeven_trigger_r * risk:
                    be_armed = True
                    current_stop = min(current_stop, be_price)
            if ltf["high"][k] >= current_stop:
                outcome, exit_price, exit_idx = "loss", current_stop, k
                break
            if ltf["low"][k] <= target_price:
                outcome, exit_price, exit_idx = "win", target_price, k
                break

    if outcome is None:
        exit_idx = end
        exit_price = ltf["close"][end]

    r = (exit_price - entry_price) / risk if direction == "bullish" else (entry_price - exit_price) / risk
    outcome = "win" if r > 0 else ("breakeven" if r == 0 else "loss")
    return outcome, exit_price, exit_idx, r


def simulate_exit_partial(ltf, entry_idx, entry_price, stop, target_price, rr_target, direction, max_hold_ltf_bars,
                           risk, partial_r, partial_fraction=0.5, move_stop_to_be_after_partial=True):
    """
    Close partial_fraction of the position once price reaches partial_r in
    favor (filled exactly at that level -- a limit-order assumption), then
    manage the remainder toward the full target. If move_stop_to_be_after_partial,
    the remainder's stop moves to entry_price once the partial fills.

    Returns (outcome, blended_r, exit_idx, partial_taken: bool, remainder_outcome).
    blended_r = partial_fraction*partial_r + (1-partial_fraction)*remainder_r,
    where remainder_r is 0 if the whole trade stopped out before partial ever filled
    (partial_fraction is irrelevant then -- the full position took the loss).
    """
    end = min(entry_idx + max_hold_ltf_bars, len(ltf) - 1)
    partial_taken = False
    current_stop = stop
    remainder_r = None
    exit_idx = end

    for k in range(entry_idx, end + 1):
        if direction == "bullish":
            if not partial_taken:
                if ltf["low"][k] <= current_stop:
                    remainder_r = (current_stop - entry_price) / risk
                    exit_idx = k
                    break
                if ltf["high"][k] >= entry_price + partial_r * risk:
                    partial_taken = True
                    if move_stop_to_be_after_partial:
                        current_stop = max(current_stop, entry_price)
                    if ltf["high"][k] >= target_price:
                        remainder_r = rr_target
                        exit_idx = k
                        break
                    continue
            else:
                if ltf["low"][k] <= current_stop:
                    remainder_r = (current_stop - entry_price) / risk
                    exit_idx = k
                    break
                if ltf["high"][k] >= target_price:
                    remainder_r = rr_target
                    exit_idx = k
                    break
        else:
            if not partial_taken:
                if ltf["high"][k] >= current_stop:
                    remainder_r = (entry_price - current_stop) / risk
                    exit_idx = k
                    break
                if ltf["low"][k] <= entry_price - partial_r * risk:
                    partial_taken = True
                    if move_stop_to_be_after_partial:
                        current_stop = min(current_stop, entry_price)
                    if ltf["low"][k] <= target_price:
                        remainder_r = rr_target
                        exit_idx = k
                        break
                    continue
            else:
                if ltf["high"][k] >= current_stop:
                    remainder_r = (entry_price - current_stop) / risk
                    exit_idx = k
                    break
                if ltf["low"][k] <= target_price:
                    remainder_r = rr_target
                    exit_idx = k
                    break

    if remainder_r is None:
        exit_idx = end
        close_price = ltf["close"][end]
        remainder_r = (close_price - entry_price) / risk if direction == "bullish" else (entry_price - close_price) / risk

    if partial_taken:
        blended_r = partial_fraction * partial_r + (1 - partial_fraction) * remainder_r
    else:
        blended_r = remainder_r  # full position took the stop before partial ever filled

    outcome = "win" if blended_r > 0 else ("breakeven" if blended_r == 0 else "loss")
    return outcome, blended_r, exit_idx, partial_taken


def find_continuation_zone(ltf, start_idx, end_idx, direction, minor_window=1, bos_fvg_tol=2):
    """
    Look for a break-of-structure after `start_idx` that leaves behind a
    fresh same-direction FVG -- the old structure level is the "liquidity
    left behind"; the FVG next to it is the zone we mark for a pyramid add.
    Returns {"top", "bottom", "bos_idx"} in GLOBAL ltf indices, or None.
    """
    if end_idx - start_idx < 6:
        return None
    sub = ltf.iloc[start_idx:end_idx + 1].reset_index(drop=True)
    minor_highs, minor_lows = find_swings(sub, window=minor_window)
    fvgs = find_fvgs(sub)
    closes = sub["close"].values

    if direction == "bullish":
        for sh in minor_highs:
            level = sub["high"].values[sh]
            for j in range(sh + 1, len(sub)):
                if closes[j] > level:
                    hit = next((g for g in fvgs if g["type"] == "bullish" and abs(g["formed_at"] - j) <= bos_fvg_tol), None)
                    if hit:
                        return {"top": hit["top"], "bottom": hit["bottom"], "bos_idx": start_idx + j}
                    break
    else:
        for sl in minor_lows:
            level = sub["low"].values[sl]
            for j in range(sl + 1, len(sub)):
                if closes[j] < level:
                    hit = next((g for g in fvgs if g["type"] == "bearish" and abs(g["formed_at"] - j) <= bos_fvg_tol), None)
                    if hit:
                        return {"top": hit["top"], "bottom": hit["bottom"], "bos_idx": start_idx + j}
                    break
    return None


def find_zone_retrace(ltf, zone, start_idx, end_idx):
    for k in range(start_idx, end_idx + 1):
        if ltf["low"][k] <= zone["top"] and ltf["high"][k] >= zone["bottom"]:
            return k
    return None


def run(symbol, htf_name, htf_bars, killzones, min_rr=3.0, max_rr=5.0,
        window_htf_bars=10, max_hold_ltf_bars=200,
        pyramiding=False, max_pyramid_legs=3, pyramid_risk_mult=0.5):
    """
    pyramiding: when True, after a base trade is taken, watch the LTF for a
    break of structure that leaves behind a fresh same-direction FVG (the
    broken level is the liquidity left behind; the FVG next to it is the
    zone). When price retraces into that zone, add a reduced-risk leg.
    Repeats up to max_pyramid_legs. A new, unrelated HTF POI trigger is
    still skipped while any leg on this symbol is open.
    """
    ltf_name = HTF_TO_LTF[htf_name]
    htf, ltf = fetch_htf_ltf(symbol, htf_name, ltf_name, htf_bars)
    print(f"HTF {htf_name}: {len(htf)} bars ({htf['time'].iloc[0]} -> {htf['time'].iloc[-1]})")
    print(f"LTF {ltf_name}: {len(ltf)} bars")

    zones = build_htf_zones(htf)
    print(f"HTF POI zones: {len(zones)}")

    trades = []
    blocked_until_time = pd.Timestamp.min.tz_localize("UTC")

    for zone in zones:
        touch = poi_touch_time(htf, zone)
        if touch is None:
            continue
        window_start_time = htf["time"][touch]
        if window_start_time < blocked_until_time:
            continue
        window_end_time = htf["time"][min(touch + window_htf_bars, len(htf) - 1)]

        mask = (ltf["time"] >= window_start_time) & (ltf["time"] <= window_end_time)
        ltf_win = ltf.loc[mask].reset_index(drop=True)
        if ltf_win.empty:
            continue

        direction = "bullish" if zone["type"] == "bullish" else "bearish"
        setup = find_confirmation(ltf_win, direction)
        if setup is None:
            continue

        # The sweep is what's supposed to be "reacting off the level" -- require
        # it to actually happen at/near the zone, not just sometime in the window.
        zone_height = zone["top"] - zone["bottom"]
        tol = max(zone_height * 0.5, zone_height)
        if not (zone["bottom"] - tol <= setup["sweep_extreme"] <= zone["top"] + tol):
            continue

        entry_local = setup["entry_idx"]
        entry_time = ltf_win["time"][entry_local]
        if not in_killzone(entry_time, killzones):
            continue

        global_entry_idx = ltf.index[ltf["time"] == entry_time]
        if len(global_entry_idx) == 0:
            continue
        gi = global_entry_idx[0]
        entry_price = ltf["open"][gi + 1] if gi + 1 < len(ltf) else ltf["close"][gi]

        if direction == "bullish":
            stop = setup["sweep_extreme"] - (setup["ote_top"] - setup["ote_bottom"]) * 0.1
            risk = entry_price - stop
        else:
            stop = setup["sweep_extreme"] + (setup["ote_top"] - setup["ote_bottom"]) * 0.1
            risk = stop - entry_price
        if risk <= 0:
            continue

        target_price, rr_target = resolve_target(zones, direction, entry_price, risk, min_rr, max_rr)
        outcome, exit_price, exit_idx, r = simulate_exit(
            ltf, gi + 1, entry_price, stop, target_price, rr_target, direction, max_hold_ltf_bars, risk)

        trades.append({
            "poi_formed_at_htf_idx": zone["formed_at"], "poi_type": zone["type"],
            "poi_top": zone["top"], "poi_bottom": zone["bottom"],
            "window_start_time": window_start_time, "window_end_time": window_end_time,
            "sweep_time": ltf_win["time"][setup["sweep_idx"]], "sweep_extreme": setup["sweep_extreme"],
            "mss_time": ltf_win["time"][setup["mss_idx"]],
            "ote_top": setup["ote_top"], "ote_bottom": setup["ote_bottom"],
            "entry_time": entry_time, "exit_time": ltf["time"][exit_idx],
            "direction": "long" if direction == "bullish" else "short",
            "entry": entry_price, "stop": stop, "target": target_price,
            "exit_price": exit_price, "outcome": outcome, "r_multiple": r,
            "rr_planned": rr_target,
            "leg_type": "base", "risk_mult": 1.0,
        })
        chain_end_time = ltf["time"][exit_idx]

        if pyramiding:
            search_from_idx = gi + 1
            search_upto_idx = min(exit_idx + max_hold_ltf_bars, len(ltf) - 1)
            for leg_number in range(1, max_pyramid_legs):
                cont_zone = find_continuation_zone(ltf, search_from_idx, search_upto_idx, direction)
                if cont_zone is None:
                    break
                retrace_idx = find_zone_retrace(ltf, cont_zone, cont_zone["bos_idx"] + 1, search_upto_idx)
                if retrace_idx is None:
                    break

                add_entry_idx = retrace_idx + 1
                if add_entry_idx >= len(ltf):
                    break
                add_entry_price = ltf["open"][add_entry_idx]
                zone_height2 = cont_zone["top"] - cont_zone["bottom"]
                buffer = zone_height2 * 0.1
                if direction == "bullish":
                    add_stop = cont_zone["bottom"] - buffer
                    add_risk = add_entry_price - add_stop
                else:
                    add_stop = cont_zone["top"] + buffer
                    add_risk = add_stop - add_entry_price
                if add_risk <= 0:
                    break

                add_target, add_rr = resolve_target(zones, direction, add_entry_price, add_risk, min_rr, max_rr)
                add_outcome, add_exit_price, add_exit_idx, add_r = simulate_exit(
                    ltf, add_entry_idx, add_entry_price, add_stop, add_target, add_rr, direction, max_hold_ltf_bars, add_risk)

                risk_mult = pyramid_risk_mult ** leg_number
                trades.append({
                    "poi_formed_at_htf_idx": zone["formed_at"], "poi_type": zone["type"],
                    "poi_top": cont_zone["top"], "poi_bottom": cont_zone["bottom"],
                    "window_start_time": ltf["time"][cont_zone["bos_idx"]], "window_end_time": ltf["time"][search_upto_idx],
                    "sweep_time": pd.NaT, "sweep_extreme": np.nan,
                    "mss_time": ltf["time"][cont_zone["bos_idx"]],
                    "ote_top": cont_zone["top"], "ote_bottom": cont_zone["bottom"],
                    "entry_time": ltf["time"][add_entry_idx], "exit_time": ltf["time"][add_exit_idx],
                    "direction": "long" if direction == "bullish" else "short",
                    "entry": add_entry_price, "stop": add_stop, "target": add_target,
                    "exit_price": add_exit_price, "outcome": add_outcome, "r_multiple": add_r,
                    "rr_planned": add_rr,
                    "leg_type": f"pyramid_{leg_number}", "risk_mult": risk_mult,
                })
                chain_end_time = max(chain_end_time, ltf["time"][add_exit_idx])
                search_from_idx = add_entry_idx
                search_upto_idx = min(add_exit_idx + max_hold_ltf_bars, len(ltf) - 1)

        blocked_until_time = chain_end_time

    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame, risk_pct=1.0, start_equity=10_000):
    if trades.empty:
        print("No trades passed HTF->LTF->killzone confirmation.")
        return None
    wins = trades[trades["r_multiple"] > 0]
    losses = trades[trades["r_multiple"] <= 0]
    win_rate = len(wins) / len(trades) * 100
    avg_r = trades["r_multiple"].mean()
    profit_factor = wins["r_multiple"].sum() / abs(losses["r_multiple"].sum()) if len(losses) else float("inf")

    risk_mults = trades["risk_mult"] if "risk_mult" in trades.columns else 1.0
    equity = [start_equity]
    for r, rm in zip(trades["r_multiple"], risk_mults if hasattr(risk_mults, "__iter__") else [1.0] * len(trades)):
        equity.append(equity[-1] * (1 + risk_pct / 100 * rm * r))
    equity = np.array(equity)
    dd = (equity - np.maximum.accumulate(equity)) / np.maximum.accumulate(equity)

    print(f"Trades:          {len(trades)}")
    print(f"Win rate:        {win_rate:.1f}%")
    print(f"Avg R:           {avg_r:.2f}")
    print(f"Profit factor:   {profit_factor:.2f}")
    print(f"Final equity:    {equity[-1]:,.2f} (start {start_equity:,.2f}, risk {risk_pct}%/trade)")
    print(f"Max drawdown:    {dd.min()*100:.1f}%")
    return equity


def main():
    ap = argparse.ArgumentParser(description="HTF-bias -> LTF-confirmation ICT backtest")
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--htf", default="H4", choices=HTF_TO_LTF.keys())
    ap.add_argument("--htf-bars", type=int, default=1500)
    ap.add_argument("--min-rr", type=float, default=3.0)
    ap.add_argument("--max-rr", type=float, default=5.0)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--out", default="htf_ltf_trades.csv")
    ap.add_argument("--plot", default="htf_ltf_equity.png")
    args = ap.parse_args()

    trades = run(args.symbol, args.htf, args.htf_bars, DEFAULT_KILLZONES,
                 min_rr=args.min_rr, max_rr=args.max_rr)
    if not trades.empty:
        trades.to_csv(args.out, index=False)
        print(f"Trade log saved to {args.out}")

    equity = summarize(trades, risk_pct=args.risk_pct)
    if equity is not None:
        import matplotlib.pyplot as plt
        plt.figure(figsize=(10, 5))
        plt.plot(equity)
        plt.title(f"{args.symbol} {args.htf}->{HTF_TO_LTF[args.htf]} HTF/LTF ICT strategy equity")
        plt.xlabel("Trade #")
        plt.ylabel("Equity")
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(args.plot)
        print(f"Equity curve saved to {args.plot}")


if __name__ == "__main__":
    main()
