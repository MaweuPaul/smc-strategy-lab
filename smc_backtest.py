"""
ICT / Smart-Money-Concepts backtest engine.

Strategy (derived from the reference screenshots):
  1. Trend bias comes from market structure (HH+HL = bullish, LH+LL = bearish).
  2. Detect Fair Value Gaps (FVG), their inversions (IFVG), and Order Blocks (OB);
     merge an OB with any overlapping same-direction FVG into a single "zone"
     (approximates the OB+BPR zones drawn on the charts).
  3. Entry trigger: a minor swing point sitting inside one of those zones gets
     swept (wicked through) and the bar closes back inside/beyond it in the
     direction of the trend bias ("turtle soup" liquidity grab).
  4. Stop: beyond the sweep wick. Target: nearest unmitigated opposing zone,
     falling back to a fixed R-multiple if none exists.

Data is read directly from a running MetaTrader5 terminal (read-only history,
no orders are ever placed).
"""

import argparse
import numpy as np
import pandas as pd
import MetaTrader5 as mt5


TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def fetch_data(symbol: str, timeframe: str, n_bars: int) -> pd.DataFrame:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
    if mt5.symbol_info(symbol) is None:
        raise RuntimeError(f"Symbol {symbol} not found on this broker/terminal")
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[timeframe], 0, n_bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No data returned for {symbol} {timeframe}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df[["time", "open", "high", "low", "close", "tick_volume"]]
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# --------------------------------------------------------------------------- #
# Structure: fractal swings
# --------------------------------------------------------------------------- #
def find_swings(df: pd.DataFrame, window: int = 2):
    """Return (swing_high_idx, swing_low_idx) confirmed `window` bars later."""
    highs, lows = df["high"].values, df["low"].values
    n = len(df)
    swing_highs, swing_lows = [], []
    for i in range(window, n - window):
        seg_h = highs[i - window:i + window + 1]
        if highs[i] == seg_h.max() and (seg_h == highs[i]).sum() == 1:
            swing_highs.append(i)
        seg_l = lows[i - window:i + window + 1]
        if lows[i] == seg_l.min() and (seg_l == lows[i]).sum() == 1:
            swing_lows.append(i)
    return swing_highs, swing_lows


def structure_bias_series(df: pd.DataFrame, swing_highs, swing_lows, swing_window: int = 2):
    """
    Bias known at each bar index, using only swings confirmed by then.
    A swing at raw index s isn't actually confirmable until s+swing_window
    (find_swings needs that many bars on both sides) -- swing_window must
    match whatever window was passed to find_swings() to produce these
    swing lists, or this leaks not-yet-knowable structure into the past.
    """
    n = len(df)
    bias = np.array(["neutral"] * n, dtype=object)
    sh_seen, sl_seen = [], []
    sh_ptr = sl_ptr = 0
    last_bias = "neutral"
    for i in range(n):
        while sh_ptr < len(swing_highs) and swing_highs[sh_ptr] + swing_window <= i:
            sh_seen.append(swing_highs[sh_ptr])
            sh_ptr += 1
        while sl_ptr < len(swing_lows) and swing_lows[sl_ptr] + swing_window <= i:
            sl_seen.append(swing_lows[sl_ptr])
            sl_ptr += 1
        if len(sh_seen) >= 2 and len(sl_seen) >= 2:
            hh = df["high"][sh_seen[-1]] > df["high"][sh_seen[-2]]
            hl = df["low"][sl_seen[-1]] > df["low"][sl_seen[-2]]
            lh = df["high"][sh_seen[-1]] < df["high"][sh_seen[-2]]
            ll = df["low"][sl_seen[-1]] < df["low"][sl_seen[-2]]
            if hh and hl:
                last_bias = "bullish"
            elif lh and ll:
                last_bias = "bearish"
        bias[i] = last_bias
    return bias


# --------------------------------------------------------------------------- #
# Fair Value Gaps + inversion
# --------------------------------------------------------------------------- #
def find_fvgs(df: pd.DataFrame):
    """3-candle imbalance. Returns list of dicts, formed_at = index i+1."""
    highs, lows = df["high"].values, df["low"].values
    fvgs = []
    for i in range(1, len(df) - 1):
        if lows[i + 1] > highs[i - 1]:
            fvgs.append({"type": "bullish", "top": lows[i + 1], "bottom": highs[i - 1],
                         "formed_at": i + 1, "status": "active", "inverted_at": None})
        elif highs[i + 1] < lows[i - 1]:
            fvgs.append({"type": "bearish", "top": lows[i - 1], "bottom": highs[i + 1],
                         "formed_at": i + 1, "status": "active", "inverted_at": None})
    return fvgs


def update_fvg_status(fvgs, df, upto_idx):
    """Mark FVGs mitigated/inverted using bars available up to `upto_idx`."""
    closes = df["close"].values
    for gap in fvgs:
        if gap["formed_at"] > upto_idx or gap["status"] == "inverted":
            continue
        for j in range(gap["formed_at"] + 1, upto_idx + 1):
            if gap["type"] == "bullish" and closes[j] < gap["bottom"]:
                gap["type"] = "bearish"  # flips to resistance -> now an IFVG
                gap["status"] = "inverted"
                gap["inverted_at"] = j
                break
            if gap["type"] == "bearish" and closes[j] > gap["top"]:
                gap["type"] = "bullish"  # flips to support -> now an IFVG
                gap["status"] = "inverted"
                gap["inverted_at"] = j
                break


# --------------------------------------------------------------------------- #
# Order blocks
# --------------------------------------------------------------------------- #
def find_order_blocks(df: pd.DataFrame, swing_highs, swing_lows, lookback: int = 6, swing_window: int = 2):
    """
    Last opposite-colour candle before a break-of-structure impulse.
    swing_window must match whatever window find_swings() used to produce
    swing_highs/swing_lows: a swing at raw index s isn't confirmable until
    s+swing_window, so testing breakouts against it before then would use
    structure information that wasn't actually knowable yet.
    """
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    obs = []
    sh_prices = {i: h[i] for i in swing_highs}
    sl_prices = {i: l[i] for i in swing_lows}
    sh_sorted = sorted(swing_highs)
    sl_sorted = sorted(swing_lows)

    def prior_swing_high(i):
        cands = [s for s in sh_sorted if s + swing_window <= i]
        return sh_prices[cands[-1]] if cands else None

    def prior_swing_low(i):
        cands = [s for s in sl_sorted if s + swing_window <= i]
        return sl_prices[cands[-1]] if cands else None

    for i in range(2, len(df)):
        ph = prior_swing_high(i)
        if ph is not None and c[i] > ph and c[i - 1] <= ph:
            for k in range(i - 1, max(i - lookback, 0) - 1, -1):
                if c[k] < o[k]:
                    obs.append({"type": "bullish", "top": h[k], "bottom": l[k],
                                "formed_at": i, "candle_idx": k, "status": "active"})
                    break
        pl = prior_swing_low(i)
        if pl is not None and c[i] < pl and c[i - 1] >= pl:
            for k in range(i - 1, max(i - lookback, 0) - 1, -1):
                if c[k] > o[k]:
                    obs.append({"type": "bearish", "top": h[k], "bottom": l[k],
                                "formed_at": i, "candle_idx": k, "status": "active"})
                    break
    return obs


def build_zones(obs, fvgs, merge_window: int = 5):
    """Merge an OB with a same-direction FVG formed nearby into one zone."""
    zones = []
    used_fvg = set()
    for ob in obs:
        top, bottom = ob["top"], ob["bottom"]
        for idx, gap in enumerate(fvgs):
            if idx in used_fvg or gap["type"] != ob["type"]:
                continue
            if abs(gap["formed_at"] - ob["formed_at"]) > merge_window:
                continue
            overlap = min(top, gap["top"]) - max(bottom, gap["bottom"])
            if overlap > 0:
                top, bottom = max(top, gap["top"]), min(bottom, gap["bottom"])
                used_fvg.add(idx)
        zones.append({"type": ob["type"], "top": top, "bottom": bottom,
                      "formed_at": ob["formed_at"], "status": "active"})
    for idx, gap in enumerate(fvgs):
        if idx not in used_fvg and gap["status"] == "active":
            zones.append({"type": gap["type"], "top": gap["top"], "bottom": gap["bottom"],
                          "formed_at": gap["formed_at"], "status": "active"})
    zones.sort(key=lambda z: z["formed_at"])
    return zones


# --------------------------------------------------------------------------- #
# Signal generation
# --------------------------------------------------------------------------- #
def in_zone(price, zone, tolerance):
    span = zone["top"] - zone["bottom"]
    pad = max(span * tolerance, 1e-9)
    return zone["bottom"] - pad <= price <= zone["top"] + pad


def generate_signals(df, swing_highs, swing_lows, bias, zones, minor_window=1, zone_tolerance=0.15):
    minor_highs, minor_lows = find_swings(df, window=minor_window)
    highs, lows, closes = df["high"].values, df["low"].values, df["close"].values

    signals = []
    swept_low = set()
    swept_high = set()

    for j in range(len(df)):
        cur_bias = bias[j]
        if cur_bias == "bullish":
            candidates = [s for s in minor_lows if s < j and s not in swept_low]
            for s in candidates[-3:]:
                level = lows[s]
                if lows[j] < level < closes[j]:
                    active_zones = [z for z in zones
                                    if z["type"] == "bullish" and z["formed_at"] < j
                                    and z["bottom"] <= closes[j] <= z["top"] + (z["top"] - z["bottom"])]
                    zone_hit = next((z for z in zones if z["type"] == "bullish" and z["formed_at"] < j
                                      and in_zone(level, z, zone_tolerance)), None)
                    if zone_hit:
                        signals.append({"idx": j, "direction": "long",
                                         "sweep_level": level, "zone": zone_hit})
                        swept_low.add(s)
                        break
        elif cur_bias == "bearish":
            candidates = [s for s in minor_highs if s < j and s not in swept_high]
            for s in candidates[-3:]:
                level = highs[s]
                if highs[j] > level > closes[j]:
                    zone_hit = next((z for z in zones if z["type"] == "bearish" and z["formed_at"] < j
                                      and in_zone(level, z, zone_tolerance)), None)
                    if zone_hit:
                        signals.append({"idx": j, "direction": "short",
                                         "sweep_level": level, "zone": zone_hit})
                        swept_high.add(s)
                        break
    return signals


# --------------------------------------------------------------------------- #
# Backtest loop
# --------------------------------------------------------------------------- #
def run_backtest(df, signals, zones, sl_buffer_atr=0.15, min_rr=1.5, fallback_rr=3.0,
                  max_hold_bars=80):
    highs, lows, closes, opens = df["high"].values, df["low"].values, df["close"].values, df["open"].values
    atr = (df["high"] - df["low"]).rolling(14).mean().fillna(method="bfill").values

    trades = []
    last_exit_idx = -1

    for sig in signals:
        j = sig["idx"]
        if j <= last_exit_idx:
            continue  # no overlapping trades
        entry_idx = j + 1
        if entry_idx >= len(df):
            continue
        entry = opens[entry_idx]
        direction = sig["direction"]
        buffer = atr[j] * sl_buffer_atr

        if direction == "long":
            stop = sig["sweep_level"] - buffer
            risk = entry - stop
            if risk <= 0:
                continue
            opposing = [z for z in zones if z["type"] == "bearish" and z["formed_at"] < j
                        and z["bottom"] > entry]
            target = min((z["bottom"] for z in opposing), default=None)
            if target is None or (target - entry) / risk < min_rr:
                target = entry + fallback_rr * risk
        else:
            stop = sig["sweep_level"] + buffer
            risk = stop - entry
            if risk <= 0:
                continue
            opposing = [z for z in zones if z["type"] == "bullish" and z["formed_at"] < j
                        and z["top"] < entry]
            target = max((z["top"] for z in opposing), default=None)
            if target is None or (entry - target) / risk < min_rr:
                target = entry - fallback_rr * risk

        outcome, exit_price, exit_idx = None, None, None
        end = min(entry_idx + max_hold_bars, len(df) - 1)
        for k in range(entry_idx, end + 1):
            if direction == "long":
                if lows[k] <= stop:
                    outcome, exit_price, exit_idx = "loss", stop, k
                    break
                if highs[k] >= target:
                    outcome, exit_price, exit_idx = "win", target, k
                    break
            else:
                if highs[k] >= stop:
                    outcome, exit_price, exit_idx = "loss", stop, k
                    break
                if lows[k] <= target:
                    outcome, exit_price, exit_idx = "win", target, k
                    break
        if outcome is None:
            exit_idx = end
            exit_price = closes[end]
            r_mult = ((exit_price - entry) / risk) if direction == "long" else ((entry - exit_price) / risk)
            outcome = "win" if r_mult > 0 else "loss"
        else:
            r_mult = 1.0 * ((target - entry) / risk if direction == "long" else (entry - target) / risk) \
                if outcome == "win" else -1.0

        trades.append({
            "signal_idx": j, "entry_idx": entry_idx, "exit_idx": exit_idx,
            "entry_time": df["time"][entry_idx], "exit_time": df["time"][exit_idx],
            "direction": direction, "entry": entry, "stop": stop, "target": target,
            "exit_price": exit_price, "outcome": outcome, "r_multiple": r_mult,
        })
        last_exit_idx = exit_idx

    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame, risk_pct=1.0, start_equity=10_000):
    if trades.empty:
        print("No trades generated.")
        return
    wins = trades[trades["r_multiple"] > 0]
    losses = trades[trades["r_multiple"] <= 0]
    win_rate = len(wins) / len(trades) * 100
    avg_r = trades["r_multiple"].mean()
    expectancy = avg_r
    profit_factor = wins["r_multiple"].sum() / abs(losses["r_multiple"].sum()) if len(losses) else float("inf")

    equity = [start_equity]
    for r in trades["r_multiple"]:
        equity.append(equity[-1] * (1 + risk_pct / 100 * r))
    equity = np.array(equity)
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max
    max_dd = drawdown.min() * 100

    print(f"Trades:          {len(trades)}")
    print(f"Win rate:        {win_rate:.1f}%")
    print(f"Avg R:           {avg_r:.2f}")
    print(f"Expectancy:      {expectancy:.2f}R per trade")
    print(f"Profit factor:   {profit_factor:.2f}")
    print(f"Final equity:    {equity[-1]:,.2f} (start {start_equity:,.2f}, risk {risk_pct}%/trade)")
    print(f"Max drawdown:    {max_dd:.1f}%")
    return equity


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="ICT/SMC liquidity-sweep backtest")
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--timeframe", default="H4", choices=TIMEFRAMES.keys())
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--out", default="trades.csv")
    ap.add_argument("--plot", default="equity.png")
    args = ap.parse_args()

    df = fetch_data(args.symbol, args.timeframe, args.bars)
    print(f"Loaded {len(df)} {args.timeframe} bars for {args.symbol} "
          f"({df['time'].iloc[0]} -> {df['time'].iloc[-1]})")

    swing_highs, swing_lows = find_swings(df, window=2)
    bias = structure_bias_series(df, swing_highs, swing_lows)

    fvgs = find_fvgs(df)
    update_fvg_status(fvgs, df, len(df) - 1)
    obs = find_order_blocks(df, swing_highs, swing_lows)
    zones = build_zones(obs, fvgs)

    signals = generate_signals(df, swing_highs, swing_lows, bias, zones)
    print(f"Generated {len(signals)} raw signals")

    trades = run_backtest(df, signals, zones)
    trades.to_csv(args.out, index=False)
    print(f"Trade log saved to {args.out}")

    equity = summarize(trades, risk_pct=args.risk_pct)

    if equity is not None:
        import matplotlib.pyplot as plt
        plt.figure(figsize=(10, 5))
        plt.plot(equity)
        plt.title(f"{args.symbol} {args.timeframe} — SMC liquidity-sweep strategy equity curve")
        plt.xlabel("Trade #")
        plt.ylabel("Equity")
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(args.plot)
        print(f"Equity curve saved to {args.plot}")


if __name__ == "__main__":
    main()
