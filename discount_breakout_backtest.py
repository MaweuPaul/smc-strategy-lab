"""
Discount-zone breakout continuation backtest (long only), EURUSD H4.

STRATEGY (hard-coded, no discretion):
  Bias   : price is in a "discount" zone -- below the 50% (equilibrium)
           level of the most recent CONFIRMED fractal swing high/low range.
  Signal : a H4 candle closes above the high of the immediately preceding
           H4 candle, while price is in the discount zone.
  Direction: long only.

Swing definition (fractal, N=5 bars each side):
  Bar i is a swing high if high[i] is the max of high[i-N .. i+N].
  Bar i is a swing low  if low[i]  is the min of low[i-N .. i+N].
  A swing is only "known" (usable for the discount-zone calc) starting at
  bar i+N -- the earliest a live trader could have confirmed it. This is
  the standard anti-look-ahead guard: never use a swing before it's
  confirmable from bars that have actually closed.

V1 -- baseline: fixed stop at signal-candle low, time exit after K candles
      (K = 3, then K = 4), no partials, no stop adjustment.
V2 -- locked-in runner: identical bias/signal/entry/stop as V1. TP trigger
      = 1:1 RR (entry + 1x initial risk). Once price touches the TP
      trigger, the stop is moved to sit exactly at the TP price (not
      breakeven). Exit only on (a) that moved stop being hit, or (b) the
      time exit (K candles) -- whichever comes first.

Both variants are tested for TWO entry timings (config ENTRY_MODES):
  "close_signal" : entry at the close of the signal candle
  "open_next"    : entry at the open of the candle after the signal candle
Both variants are tested for TWO time-exit horizons (config TIME_EXITS):
  3 candles and 4 candles after entry.

No parameter here was tuned against the output -- all four are fixed
before running, per the spec. If you want to test a different variant,
add it as a clearly separate, labeled run -- don't edit these to chase a
better number after seeing results.
"""

import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime

SYMBOL = "EURUSD"
TF = mt5.TIMEFRAME_H4
SWING_N = 5                       # fractal confirmation window, each side
TIME_EXITS = [3, 4]                # candles after entry, time-based exit
ENTRY_MODES = ["close_signal", "open_next"]
MIN_TRADES = 100
HISTORY_FROM = datetime(2000, 1, 1)


# ---------------------------------------------------------------- data ----

def _drop_incomplete_last_bar(df, bar_seconds, now_utc):
    """MT5 can return the still-forming current bar when queried up to
    now(). Drop the last row if its close time hasn't happened yet, so a
    changing/incomplete candle never feeds signals or bias."""
    if len(df) == 0:
        return df
    last_close = df["time"].iloc[-1] + pd.Timedelta(seconds=bar_seconds)
    if last_close > now_utc:
        return df.iloc[:-1].reset_index(drop=True)
    return df


def fetch_h4():
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    now = datetime.now()
    rates = mt5.copy_rates_range(SYMBOL, TF, HISTORY_FROM, now)
    mt5.shutdown()
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No H4 data returned for {SYMBOL}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df[["time", "open", "high", "low", "close"]].reset_index(drop=True)
    now_utc = pd.Timestamp(now, tz="UTC")
    df = _drop_incomplete_last_bar(df, 4 * 3600, now_utc)
    return df


# ------------------------------------------------------- swings / bias ----

def compute_confirmed_swings(df, n=SWING_N):
    """Returns arrays: last_swing_high[i], last_swing_low[i] = the most
    recent swing high/low CONFIRMED as of (available at) bar i's close.
    A swing formed at index j is only usable from index j+n onward."""
    high = df["high"].values
    low = df["low"].values
    m = len(df)

    is_sh = np.zeros(m, dtype=bool)
    is_sl = np.zeros(m, dtype=bool)
    for j in range(n, m - n):
        window_h = high[j - n: j + n + 1]
        window_l = low[j - n: j + n + 1]
        if high[j] == window_h.max() and (window_h == high[j]).sum() == 1:
            is_sh[j] = True
        if low[j] == window_l.min() and (window_l == low[j]).sum() == 1:
            is_sl[j] = True

    last_sh = np.full(m, np.nan)
    last_sl = np.full(m, np.nan)
    cur_sh, cur_sl = np.nan, np.nan
    for i in range(m):
        confirm_idx = i - n
        if confirm_idx >= 0:
            if is_sh[confirm_idx]:
                cur_sh = high[confirm_idx]
            if is_sl[confirm_idx]:
                cur_sl = low[confirm_idx]
        last_sh[i] = cur_sh
        last_sl[i] = cur_sl
    return last_sh, last_sl


def build_signals(df):
    last_sh, last_sl = compute_confirmed_swings(df)
    df = df.copy()
    df["swing_high"] = last_sh
    df["swing_low"] = last_sl
    df["eq"] = (df["swing_high"] + df["swing_low"]) / 2
    df["discount"] = df["close"] < df["eq"]
    df["prev_high"] = df["high"].shift(1)
    df["signal"] = df["discount"] & (df["close"] > df["prev_high"])
    df["signal"] = df["signal"].fillna(False)
    return df


# ------------------------------------------------------------ regime ------

def build_regime(df, ma_period=200):
    """Simple regime filter, per spec's own suggestion: price relative to
    a 200-period MA computed on a higher timeframe (Daily), projected onto
    the H4 series it was current for.

    MT5 stamps a daily bar with its OPEN time, not its close -- so a daily
    row timestamped 2024-05-10 00:00 isn't actually closed (its close/MA
    aren't knowable) until 2024-05-11 00:00. Availability is shifted
    forward by one full day before the as-of merge so an H4 bar can only
    see the previous day's completed close/MA, never same-day data.
    """
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    now = datetime.now()
    d1 = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_D1, HISTORY_FROM, now)
    mt5.shutdown()
    d1 = pd.DataFrame(d1)
    d1["time"] = pd.to_datetime(d1["time"], unit="s", utc=True)
    now_utc = pd.Timestamp(now, tz="UTC")
    d1 = _drop_incomplete_last_bar(d1, 24 * 3600, now_utc)
    d1["ma"] = d1["close"].rolling(ma_period).mean()
    d1 = d1[["time", "ma"]].dropna()
    d1["available_at"] = (d1["time"] + pd.Timedelta(days=1)).astype(df["time"].dtype)

    # as-of merge: each H4 bar gets the most recent CLOSED daily MA value
    df = df.sort_values("time")
    d1 = d1.sort_values("available_at")
    merged = pd.merge_asof(df, d1, left_on="time", right_on="available_at", direction="backward")
    regime = np.where(merged["close"] > merged["ma"], "trend_up",
                       np.where(merged["close"] < merged["ma"], "trend_down", "unknown"))
    return pd.Series(regime, index=df.index).fillna("unknown")


# ------------------------------------------------------------- trades -----

def simulate(df, entry_mode, time_exit_k, version):
    """version: 'v1' (fixed time exit) or 'v2' (locked-in runner)."""
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    t = df["time"].values
    sig = df["signal"].values
    m = len(df)

    trades = []
    i = 0
    while i < m:
        if not sig[i]:
            i += 1
            continue

        # signal candle is index i; entry timing:
        if entry_mode == "close_signal":
            entry_idx = i
            entry_price = c[i]
        else:  # open_next
            entry_idx = i + 1
            if entry_idx >= m:
                break
            entry_price = o[entry_idx]

        stop = l[i]  # low of the signal candle, per spec
        risk = entry_price - stop
        if risk <= 0:
            i += 1
            continue

        deadline_idx = entry_idx + time_exit_k
        if deadline_idx >= m:
            i += 1
            continue

        exit_price = None
        exit_idx = None
        exit_reason = None
        touched_tp = False
        gapped = False
        tp = entry_price + risk  # 1:1 RR trigger (used by v2 only)
        current_stop = stop

        for k in range(entry_idx, deadline_idx + 1):
            if k == entry_idx:
                # For open_next, the fill happens at this bar's open, so
                # the rest of this same candle (low/high after the open)
                # can still trigger the stop or TP -- it must be tested
                # too, not skipped. For close_signal the entry candle is
                # bar i itself, already fully closed by the time of entry
                # (its low IS the stop by construction), so there is
                # nothing left in it to test.
                if entry_mode == "open_next":
                    if l[k] <= current_stop:
                        exit_price = current_stop
                        exit_idx = k
                        exit_reason = "stop"
                        break
                    if version == "v2" and h[k] >= tp:
                        touched_tp = True
                        current_stop = tp
                        if l[k] <= current_stop:
                            exit_price = current_stop
                            exit_idx = k
                            exit_reason = "locked_stop"
                            break
                if k == deadline_idx:
                    exit_price = c[k]
                    exit_idx = k
                    exit_reason = "time"
                continue

            # a later candle can gap straight through the stop -- fill at
            # that candle's open, not at the stale stop/TP price, since a
            # live order would never get the old price once the market
            # has already opened beyond it
            if o[k] <= current_stop:
                exit_price = o[k]
                exit_idx = k
                exit_reason = "locked_stop" if touched_tp else "stop"
                gapped = True
                break
            if l[k] <= current_stop:
                exit_price = current_stop
                exit_idx = k
                exit_reason = "locked_stop" if touched_tp else "stop"
                break
            if version == "v2" and not touched_tp and h[k] >= tp:
                touched_tp = True
                current_stop = tp
                # same bar could also have already dipped back to the new
                # stop -- re-check this bar's low against the new level
                if l[k] <= current_stop:
                    exit_price = current_stop
                    exit_idx = k
                    exit_reason = "locked_stop"
                    break
            if k == deadline_idx:
                exit_price = c[k]
                exit_idx = k
                exit_reason = "time"

        r_multiple = (exit_price - entry_price) / risk
        trades.append({
            "entry_time": t[entry_idx],
            "exit_time": t[exit_idx],
            "entry_price": entry_price,
            "exit_price": exit_price,
            "stop": stop,
            "risk": risk,
            "r_multiple": r_multiple,
            "exit_reason": exit_reason,
            "touched_tp": touched_tp,
            "gapped_exit": gapped,
            "year": pd.Timestamp(t[entry_idx]).year,
        })

        # one trade at a time -- next signal search resumes after this
        # trade's exit bar, so overlapping signals don't create phantom
        # concurrent positions
        i = exit_idx + 1

    return pd.DataFrame(trades)


# ------------------------------------------------------------- metrics ----

def max_consecutive_losses(r_series):
    worst = cur = 0
    for r in r_series:
        if r < 0:
            cur += 1
            worst = max(worst, cur)
        else:
            cur = 0
    return worst


def max_drawdown_r(r_series):
    cum = np.cumsum(r_series)
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    return dd.min() if len(dd) else 0.0


def summarize(trades, label):
    n = len(trades)
    print(f"\n--- {label} ---")
    if n < MIN_TRADES:
        print(f"Only {n} qualifying trades (< {MIN_TRADES}). Not reporting conclusions.")
        return None
    r = trades["r_multiple"].values
    wins = (r > 0).sum()
    stats = {
        "n_trades": n,
        "win_rate": wins / n,
        "expectancy_R": r.mean(),
        "median_R": np.median(r),
        "best_R": r.max(),
        "worst_R": r.min(),
        "max_consec_losses": max_consecutive_losses(r),
        "max_drawdown_R": max_drawdown_r(r),
    }
    for k, v in stats.items():
        if isinstance(v, float):
            print(f"  {k:20s}: {v:.3f}")
        else:
            print(f"  {k:20s}: {v}")

    if "touched_tp" in trades.columns and trades["touched_tp"].any():
        touched = trades[trades["touched_tp"]]
        n_touch = len(touched)
        n_locked_stop = (touched["exit_reason"] == "locked_stop").sum()
        n_continued = n_touch - n_locked_stop
        print(f"  TP trigger touched   : {n_touch}/{n} ({n_touch/n:.1%})")
        print(f"  -> continued past TP : {n_continued} ({n_continued/n_touch:.1%} of touches)")
        print(f"  -> reversed to stop  : {n_locked_stop} ({n_locked_stop/n_touch:.1%} of touches)")

    if "gapped_exit" in trades.columns:
        n_gapped = trades["gapped_exit"].sum()
        print(f"  Gapped stop fills    : {n_gapped}/{n} ({n_gapped/n:.1%})")

    print("  By year:")
    for yr, grp in trades.groupby("year"):
        gr = grp["r_multiple"].values
        print(f"    {yr}: n={len(gr):3d}  win%={100*(gr>0).mean():5.1f}  avgR={gr.mean():+.3f}")

    return stats


def summarize_by_regime(trades, regime_series, df, label):
    lut = pd.DataFrame({"time": df["time"], "regime": regime_series.values}).sort_values("time")
    tr = trades.copy()
    tr["entry_time"] = pd.to_datetime(tr["entry_time"], utc=True)
    tr = tr.sort_values("entry_time")
    merged = pd.merge_asof(tr, lut, left_on="entry_time", right_on="time", direction="backward")
    print(f"  By regime ({label}):")
    for reg, grp in merged.groupby("regime"):
        gr = grp["r_multiple"].values
        if len(gr) == 0:
            continue
        print(f"    {reg:10s}: n={len(gr):3d}  win%={100*(gr>0).mean():5.1f}  avgR={gr.mean():+.3f}")


# --------------------------------------------------------------- main -----

def main():
    print(f"Fetching {SYMBOL} H4 data from MT5 ({HISTORY_FROM.date()} -> now)...")
    df = fetch_h4()
    print(f"Got {len(df)} H4 candles, {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")

    df = build_signals(df)
    n_signals = df["signal"].sum()
    print(f"Raw signal count (before entry/exit simulation, no dedup): {n_signals}")

    regime = build_regime(df)

    for entry_mode in ENTRY_MODES:
        for k in TIME_EXITS:
            v1 = simulate(df, entry_mode, k, "v1")
            label_v1 = f"V1 baseline | entry={entry_mode} | time_exit={k} candles"
            s1 = summarize(v1, label_v1)
            if s1:
                summarize_by_regime(v1, regime, df, label_v1)

            v2 = simulate(df, entry_mode, k, "v2")
            label_v2 = f"V2 locked-in runner | entry={entry_mode} | time_exit={k} candles"
            s2 = summarize(v2, label_v2)
            if s2:
                summarize_by_regime(v2, regime, df, label_v2)


if __name__ == "__main__":
    main()
