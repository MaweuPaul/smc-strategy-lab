"""
P404 Sweep Reversal -- standalone Python backtest, rebuilt from a TradingView
Pine strategy spec (liquidity sweep + EMA50 trend filter + partial TP/BE
management). All data comes from a local MT5 terminal.

ASSUMPTIONS (flagged, not tuned -- confirm if any of these are wrong):
  - MT5 candle timestamps are broker SERVER time, not true UTC. The broker's
    fixed UTC offset is measured once at runtime (latest tick time vs the
    machine's real UTC clock) and held constant for the whole backtest
    window. If the broker's own DST calendar differs from the US's for a
    few weeks a year, Asia/London hour windows can be off by an hour during
    that window -- a real but small and documented limitation.
  - "NAS100 (US100 CFD)" maps to the MT5 symbol "USTEC", matching this
    project's existing symbol alias (see frontend/src/Shared.jsx).
  - "Previous completed calendar week" = ISO week (Monday-Sunday, NY time).
  - Daily/weekly OHLC for PDH/PDL/PWH/PWL are derived by resampling the same
    M15 series in NY time (not fetched as separate MT5 D1/W1 bars), so the
    day/week boundary is consistent with the NY-time framework the whole
    strategy is built on -- an MT5 D1 bar boundary is the broker's own
    server-day cutoff, which would silently drift from NY midnight.
  - A candle's "NY-time hour" is read from its OPEN timestamp (M15 bars
    align to :00/:15/:30/:45, so this has no edge-case ambiguity).
  - No explicit time-based exit is specified: a position with no time exit,
    stays open until stop or TP2 resolves, or until the data runs out.
"""

import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, timezone

from smc_backtest import find_swings, structure_bias_series

SYMBOLS = ["USTEC", "EURUSD", "XAUUSD", "GBPUSD", "USDJPY"]
NY_TZ = "America/New_York"
START = datetime(2000, 1, 1)  # MT5 just returns whatever it actually has (~2018+ for these symbols)
END = datetime(2026, 9, 8)

# --- toggles --- (currently isolated to Asia-only liquidity, per experiment)
USE_PDH_PDL = False
USE_PWH_PWL = False
REQUIRE_CONFIRMATION_CANDLE = True
USE_TREND_FILTER = True

BOS_SWING_WINDOW = 2  # confirmed-fractal window for structure/trend bias
USE_EQL_EQH = False
EQL_TOLERANCE_ATR_MULT = 0.15  # how close two swing lows/highs must be (x M15 ATR14) to count as "equal"

HTF_TIMEFRAME = mt5.TIMEFRAME_M15  # PHASE 404 does liquidity/sweep/structure all on the 15m chart -- see compute_htf_bias
HTF_BAR_SECONDS = 3600

EMA_LEN = 50
ATR_LEN = 14
ATR_BUFFER_MULT = 0.25
TP1_R = 1.5
TP2_FALLBACK_R = 3.0
TP1_CLOSE_FRAC = 0.5

RISK_PCT = 0.015
MAX_LEVERAGE = 5.0
COMMISSION_RATE = 0.0005  # 0.05% per fill
START_EQUITY = 100_000.0

ASIA_HOURS = lambda h: (h >= 19) or (h < 4)   # NY hour in [19:00, 04:00)
LONDON_HOURS = lambda h: 2 <= h < 5           # NY hour in [02:00, 05:00)


# ---------------------------------------------------------------- data ----

def _server_utc_offset_hours(symbol):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol} -- is it in Market Watch?")
    server_time = datetime.fromtimestamp(tick.time, tz=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    return round((server_time - now_utc).total_seconds() / 3600)


def fetch_m15(symbol, start, end):
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    if not mt5.symbol_select(symbol, True):
        mt5.shutdown()
        raise RuntimeError(f"Could not select symbol {symbol}")
    offset_h = _server_utc_offset_hours(symbol)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start, end)
    mt5.shutdown()
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No M15 data for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(rates)[["time", "open", "high", "low", "close"]]
    # server-labeled epoch -> true UTC -> NY
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=offset_h)
    df["time_ny"] = df["time"].dt.tz_convert(NY_TZ)
    df["hour_ny"] = df["time_ny"].dt.hour
    df = df.sort_values("time").reset_index(drop=True)
    return df


def fetch_htf(symbol, start, end, timeframe):
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    if not mt5.symbol_select(symbol, True):
        mt5.shutdown()
        raise RuntimeError(f"Could not select symbol {symbol}")
    offset_h = _server_utc_offset_hours(symbol)
    rates = mt5.copy_rates_range(symbol, timeframe, start, end)
    mt5.shutdown()
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No HTF data for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(rates)[["time", "open", "high", "low", "close"]]
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=offset_h)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def structure_break_bias_series(df, swing_highs, swing_lows, swing_window):
    """CHoCH-style bias: flips the moment price CLOSES beyond the single
    most recently confirmed opposing swing extreme, not smc_backtest.py's
    structure_bias_series (which needs a matched HH+HL / LH+LL PAIR before
    flipping). That pair requirement can leave the label stale for hours
    during a clean, sustained move -- e.g. a swing high from the old
    uptrend still stands, so even as price makes lower low after lower
    low, bias keeps reading "bullish" until a second lower high also
    happens to get confirmed. This reacts within one bar of an actual
    break of structure instead."""
    n = len(df)
    close, high, low = df["close"].values, df["high"].values, df["low"].values
    bias = np.array(["neutral"] * n, dtype=object)
    sh_ptr = sl_ptr = 0
    last_bias = "neutral"
    last_swing_high = last_swing_low = np.nan
    for i in range(n):
        while sh_ptr < len(swing_highs) and swing_highs[sh_ptr] + swing_window <= i:
            last_swing_high = high[swing_highs[sh_ptr]]
            sh_ptr += 1
        while sl_ptr < len(swing_lows) and swing_lows[sl_ptr] + swing_window <= i:
            last_swing_low = low[swing_lows[sl_ptr]]
            sl_ptr += 1
        if not np.isnan(last_swing_low) and close[i] < last_swing_low:
            last_bias = "bearish"
        elif not np.isnan(last_swing_high) and close[i] > last_swing_high:
            last_bias = "bullish"
        bias[i] = last_bias
    return bias


def compute_htf_bias(df15, symbol, start, end):
    """Trend/structure bias, per the actual PHASE 404 rules -- liquidity,
    sweep, AND the structure-shift confirmation are ALL read off the 15m
    chart itself (the README's 7-step flow never introduces a separate
    higher timeframe for bias; that was an experiment layered on top of
    the spec, not part of it). Reverted from a separate H1 fetch back to
    computing structure_break_bias_series directly on the M15 series.

    Still uses the CHoCH-style single-break flip (not smc_backtest.py's
    HH+HL/LH+LL pair version) -- confirmed by inspection that the pair
    version can hold a stale bias for hours through a clean sustained
    move, which is what let a long fire straight through an obvious
    decline earlier. bos_bias at M15 bar i reflects only that bar's own
    already-closed data, same non-repainting convention as every other
    per-bar signal in this file -- no separate as-of merge needed since
    the series being read IS the series being decided on."""
    if HTF_TIMEFRAME == mt5.TIMEFRAME_M15:
        swing_highs, swing_lows = find_swings(df15, window=BOS_SWING_WINDOW)
        df15 = df15.copy()
        df15["bos_bias"] = structure_break_bias_series(df15, swing_highs, swing_lows, swing_window=BOS_SWING_WINDOW)
        return df15

    htf = fetch_htf(symbol, start, end, HTF_TIMEFRAME)
    swing_highs, swing_lows = find_swings(htf, window=BOS_SWING_WINDOW)
    htf["bias"] = structure_break_bias_series(htf, swing_highs, swing_lows, swing_window=BOS_SWING_WINDOW)
    htf["available_at"] = htf["time"] + pd.Timedelta(seconds=HTF_BAR_SECONDS)

    df15 = df15.sort_values("time")
    lut = htf[["available_at", "bias"]].sort_values("available_at")
    merged = pd.merge_asof(df15, lut, left_on="time", right_on="available_at", direction="backward")
    df15 = df15.copy()
    df15["bos_bias"] = merged["bias"].fillna("neutral").values
    return df15


# ------------------------------------------------------- liquidity levels -

def compute_daily_weekly_levels(df):
    """PDH/PDL/PWH/PWL from the M15 series itself, resampled by NY calendar
    day / ISO week, shifted by one period so only the PREVIOUS completed
    day/week is ever visible (non-repainting)."""
    d = df.copy()
    d["ny_date"] = d["time_ny"].dt.date
    daily = d.groupby("ny_date").agg(high=("high", "max"), low=("low", "min"))
    daily["pdh"] = daily["high"].shift(1)
    daily["pdl"] = daily["low"].shift(1)

    iso = d["time_ny"].dt.isocalendar()
    d["ny_week"] = list(zip(iso["year"], iso["week"]))
    weekly = d.groupby("ny_week").agg(high=("high", "max"), low=("low", "min"))
    weekly["pwh"] = weekly["high"].shift(1)
    weekly["pwl"] = weekly["low"].shift(1)

    df = df.copy()
    df["pdh"] = d["ny_date"].map(daily["pdh"])
    df["pdl"] = d["ny_date"].map(daily["pdl"])
    df["pwh"] = d["ny_week"].map(weekly["pwh"])
    df["pwl"] = d["ny_week"].map(weekly["pwl"])
    return df


def compute_asia_levels(df):
    """Running Asia high/low, frozen at session end and held constant
    until the NEXT session ends. NaN before the first session completes."""
    hours = df["hour_ny"].values
    highs = df["high"].values
    lows = df["low"].values
    m = len(df)

    asia_high = np.full(m, np.nan)
    asia_low = np.full(m, np.nan)
    in_session = False
    run_high = run_low = np.nan
    frozen_high = frozen_low = np.nan

    for i in range(m):
        is_asia = ASIA_HOURS(hours[i])
        if is_asia and not in_session:
            run_high, run_low = highs[i], lows[i]
            in_session = True
        elif is_asia and in_session:
            run_high = max(run_high, highs[i])
            run_low = min(run_low, lows[i])
        elif not is_asia and in_session:
            frozen_high, frozen_low = run_high, run_low
            in_session = False
        asia_high[i] = frozen_high
        asia_low[i] = frozen_low

    df = df.copy()
    df["asia_high"] = asia_high
    df["asia_low"] = asia_low
    return df


def compute_equal_liquidity(df, swing_highs, swing_lows, swing_window, atr):
    """Equal Highs (EQH) / Equal Lows (EQL): two confirmed swings of the
    same type within EQL_TOLERANCE_ATR_MULT x ATR14 of each other mark an
    obvious resting-liquidity pool (stops cluster just beyond the level
    retail sees as "the same" high/low). The pool activates at the SECOND
    swing's confirmation bar (non-repainting -- both swings must already
    be confirmable), held constant like Asia/PDH until swept, then
    cleared until a fresh pair forms. Uses the tighter of the two matched
    prices (the higher low / lower high) so a sweep must actually clear
    both stops, not just one."""
    n = len(df)
    lows, highs, closes = df["low"].values, df["high"].values, df["close"].values
    atr_vals = atr.values
    eql = np.full(n, np.nan)
    eqh = np.full(n, np.nan)

    sl_confirm_bar = {idx + swing_window: lows[idx] for idx in swing_lows}
    sh_confirm_bar = {idx + swing_window: highs[idx] for idx in swing_highs}

    pending_lows, pending_highs = [], []
    cur_eql = cur_eqh = np.nan
    for i in range(n):
        if i in sl_confirm_bar:
            price = sl_confirm_bar[i]
            tol = EQL_TOLERANCE_ATR_MULT * atr_vals[i] if not np.isnan(atr_vals[i]) else 0
            match = next((p for p in pending_lows if abs(p - price) <= tol), None)
            if match is not None:
                cur_eql = max(match, price)  # tighter (higher) of the two lows
                pending_lows = []
            else:
                pending_lows.append(price)
                pending_lows = pending_lows[-5:]
        if i in sh_confirm_bar:
            price = sh_confirm_bar[i]
            tol = EQL_TOLERANCE_ATR_MULT * atr_vals[i] if not np.isnan(atr_vals[i]) else 0
            match = next((p for p in pending_highs if abs(p - price) <= tol), None)
            if match is not None:
                cur_eqh = min(match, price)  # tighter (lower) of the two highs
                pending_highs = []
            else:
                pending_highs.append(price)
                pending_highs = pending_highs[-5:]

        # record the level active INTO this bar first (so the bar that
        # actually sweeps it can still see it), only clear from the next
        # bar onward
        eql[i] = cur_eql
        eqh[i] = cur_eqh
        if not np.isnan(cur_eql) and lows[i] < cur_eql and closes[i] > cur_eql:
            cur_eql = np.nan
        if not np.isnan(cur_eqh) and highs[i] > cur_eqh and closes[i] < cur_eqh:
            cur_eqh = np.nan

    df = df.copy()
    df["eql"] = eql
    df["eqh"] = eqh
    return df


# ------------------------------------------------------- indicators -------

def compute_atr14(df, n=ATR_LEN):
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def compute_ema(series, n):
    return series.ewm(span=n, adjust=False, min_periods=n).mean()


# ------------------------------------------------------------- signals ----

def build_signals(df):
    df = df.copy().reset_index(drop=True)  # find_swings/structure_bias_series assume positional == label
    df["atr14"] = compute_atr14(df)
    df["ema50"] = compute_ema(df["close"], EMA_LEN)

    # Trend bias ("bos_bias") is expected to already be on df -- see
    # compute_htf_bias, called upstream on the H1 series and merged onto
    # this M15 df by each H1 bar's close time. Not recomputed here.
    if "bos_bias" not in df.columns:
        raise ValueError("build_signals expects df['bos_bias'] pre-populated (call compute_htf_bias first)")

    if USE_EQL_EQH:
        swing_highs, swing_lows = find_swings(df, window=BOS_SWING_WINDOW)
        df = compute_equal_liquidity(df, swing_highs, swing_lows, BOS_SWING_WINDOW, df["atr14"])

    # EQH/EQL are just one more liquidity pool, parallel to and OR'd with
    # Asia/PDH/PDL/PWH/PWL -- sweeping any one of them (including EQH/EQL)
    # is a valid trigger, same as the others. No gating, no exclusivity.
    high_levels = ["asia_high"] + (["pdh"] if USE_PDH_PDL else []) + (["pwh"] if USE_PWH_PWL else []) + (["eqh"] if USE_EQL_EQH else [])
    low_levels = ["asia_low"] + (["pdl"] if USE_PDH_PDL else []) + (["pwl"] if USE_PWH_PWL else []) + (["eql"] if USE_EQL_EQH else [])

    bearish_confirm = (df["close"] < df["open"]) if REQUIRE_CONFIRMATION_CANDLE else True
    bullish_confirm = (df["close"] > df["open"]) if REQUIRE_CONFIRMATION_CANDLE else True

    short_raw = pd.Series(False, index=df.index)
    for lvl in high_levels:
        swept = (df["high"] > df[lvl]) & (df["close"] < df[lvl])
        short_raw |= swept.fillna(False)
    short_raw &= bearish_confirm

    long_raw = pd.Series(False, index=df.index)
    for lvl in low_levels:
        swept = (df["low"] < df[lvl]) & (df["close"] > df[lvl])
        long_raw |= swept.fillna(False)
    long_raw &= bullish_confirm

    in_london = df["hour_ny"].apply(LONDON_HOURS)
    short_raw &= in_london
    long_raw &= in_london

    if USE_TREND_FILTER:
        long_raw &= df["bos_bias"] == "bullish"
        short_raw &= df["bos_bias"] == "bearish"

    df["long_signal"] = long_raw.fillna(False)
    df["short_signal"] = short_raw.fillna(False)
    df["high_levels_avail"] = [tuple(high_levels)] * len(df)
    df["low_levels_avail"] = [tuple(low_levels)] * len(df)
    return df


# ------------------------------------------------------------- trades -----

def _nearest_tp2(row, direction):
    entry = row["entry_price"]
    if direction == "long":
        cands = [row[l] for l in row["high_levels_avail"] if pd.notna(row[l]) and row[l] > entry]
    else:
        cands = [row[l] for l in row["low_levels_avail"] if pd.notna(row[l]) and row[l] < entry]
    if not cands:
        return None
    return min(cands, key=lambda x: abs(x - entry))


def simulate(df):
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    t = df["time"].values
    atr = df["atr14"].values
    long_sig = df["long_signal"].values
    short_sig = df["short_signal"].values
    m = len(df)

    equity = START_EQUITY
    equity_curve = [equity]
    trades = []

    i = 0
    while i < m:
        direction = None
        if long_sig[i]:
            direction = "long"
        elif short_sig[i]:
            direction = "short"
        if direction is None or np.isnan(atr[i]):
            i += 1
            continue

        sign = 1 if direction == "long" else -1
        entry_idx = i
        entry_price = c[i]
        buffer = ATR_BUFFER_MULT * atr[i]
        stop = (l[i] - buffer) if direction == "long" else (h[i] + buffer)
        r = sign * (entry_price - stop)
        if r <= 0:
            i += 1
            continue

        tp1 = entry_price + sign * TP1_R * r
        row = df.iloc[i]
        tp2 = _nearest_tp2({**row.to_dict(), "entry_price": entry_price}, direction)
        if tp2 is None:
            tp2 = entry_price + sign * TP2_FALLBACK_R * r

        risk_amount = equity * RISK_PCT
        qty_risk = risk_amount / r
        qty_lev = (equity * MAX_LEVERAGE) / entry_price
        qty = min(qty_risk, qty_lev)

        entry_notional = qty * entry_price
        entry_commission = entry_notional * COMMISSION_RATE
        equity -= entry_commission

        tp1_hit = False
        remaining_stop = stop
        target = tp1
        qty_remaining = qty
        exit_events = []  # list of (idx, price, qty_fraction_of_original, reason)

        resolved = False
        for k in range(entry_idx + 1, m):
            adverse = l[k] if direction == "long" else h[k]
            favorable = h[k] if direction == "long" else l[k]

            stop_hit = sign * (adverse - remaining_stop) <= 0
            target_hit = sign * (favorable - target) >= 0

            if not tp1_hit:
                if stop_hit:
                    exit_events.append((k, remaining_stop, 1.0, "stop"))
                    resolved = True
                    break
                if target_hit:
                    exit_events.append((k, tp1, TP1_CLOSE_FRAC, "tp1"))
                    tp1_hit = True
                    remaining_stop = entry_price  # breakeven on remaining half
                    target = tp2
                    qty_remaining = qty * (1 - TP1_CLOSE_FRAC)
                    # same bar could also resolve the remaining leg -- stop
                    # first on any further ambiguity, per spec
                    adverse2 = adverse
                    favorable2 = favorable
                    if sign * (adverse2 - remaining_stop) <= 0:
                        exit_events.append((k, remaining_stop, 1 - TP1_CLOSE_FRAC, "breakeven_stop"))
                        resolved = True
                        break
                    if sign * (favorable2 - target) >= 0:
                        exit_events.append((k, target, 1 - TP1_CLOSE_FRAC, "tp2"))
                        resolved = True
                        break
                continue
            else:
                if stop_hit:
                    exit_events.append((k, remaining_stop, 1 - TP1_CLOSE_FRAC, "breakeven_stop"))
                    resolved = True
                    break
                if target_hit:
                    exit_events.append((k, target, 1 - TP1_CLOSE_FRAC, "tp2"))
                    resolved = True
                    break

        if not resolved:
            # data ran out with the trade still open -- close at last close
            last_idx = m - 1
            frac_left = 1.0 if not tp1_hit else (1 - TP1_CLOSE_FRAC)
            exit_events.append((last_idx, c[last_idx], frac_left, "data_end"))

        pnl_total = 0.0
        last_exit_idx = entry_idx
        for (idx, price, frac, reason) in exit_events:
            leg_qty = qty * frac
            leg_pnl = sign * (price - entry_price) * leg_qty
            leg_commission = abs(leg_qty * price) * COMMISSION_RATE
            pnl_total += leg_pnl - leg_commission
            last_exit_idx = max(last_exit_idx, idx)

        equity += pnl_total
        equity_curve.append(equity)

        trades.append({
            "symbol": None,
            "direction": direction,
            "entry_time": t[entry_idx],
            "entry_price": entry_price,
            "stop": stop,
            "r": r,
            "tp1": tp1,
            "tp2": tp2,
            "exit_events": exit_events,
            "pnl": pnl_total,
            "equity_after": equity,
            "r_multiple": pnl_total / risk_amount if risk_amount else np.nan,
        })

        i = last_exit_idx + 1

    return pd.DataFrame(trades), equity_curve


# ------------------------------------------------------------- metrics ----

def max_drawdown_pct(curve):
    vals = np.asarray(curve)
    peak = np.maximum.accumulate(vals)
    dd = (vals - peak) / peak
    return dd.min() * 100


def summarize(trades, curve, symbol):
    print(f"\n=== {symbol} ===")
    n = len(trades)
    if n == 0:
        print("No trades.")
        return
    wins = (trades["pnl"] > 0).sum()
    profit = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    loss = -trades.loc[trades["pnl"] < 0, "pnl"].sum()
    print(f"  trades          : {n}")
    print(f"  win_rate        : {100*wins/n:.1f}%")
    print(f"  avg_r           : {trades['r_multiple'].mean():.3f}")
    print(f"  profit_factor   : {profit/loss if loss else float('nan'):.3f}")
    print(f"  max_drawdown    : {max_drawdown_pct(curve):.2f}%")
    print(f"  final_equity    : ${curve[-1]:,.2f}")
    print(f"  total_return    : {100*(curve[-1]/START_EQUITY - 1):.2f}%")
    reasons = [ev[3] for evs in trades["exit_events"] for ev in evs]
    from collections import Counter
    print(f"  exit reasons    : {dict(Counter(reasons))}")


# --------------------------------------------------------------- main -----

def main():
    for symbol in SYMBOLS:
        print(f"\nFetching {symbol} M15 {START.date()} -> {END.date()} ...")
        df = fetch_m15(symbol, START, END)
        print(f"  {len(df)} M15 candles, {df['time_ny'].iloc[0]} -> {df['time_ny'].iloc[-1]} (NY)")
        df = compute_daily_weekly_levels(df)
        df = compute_asia_levels(df)
        df = compute_htf_bias(df, symbol, START, END)
        df = build_signals(df)
        n_long = df["long_signal"].sum()
        n_short = df["short_signal"].sum()
        print(f"  raw signals (pre one-at-a-time filtering): long={n_long} short={n_short}")

        trades, curve = simulate(df)
        if not trades.empty:
            trades["symbol"] = symbol
        summarize(trades, curve, symbol)


if __name__ == "__main__":
    main()
