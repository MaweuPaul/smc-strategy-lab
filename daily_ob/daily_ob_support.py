"""Availability, conservative OHLC execution and cash accounting for Daily OB."""

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # repo root, for shared modules (smc_backtest, etc.)

import numpy as np
import pandas as pd

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30,
              "H1": 60, "H4": 240, "D1": 1440}


def completed_bars(df, timeframe, as_of=None):
    result = df.copy().reset_index(drop=True)
    result["time"] = pd.to_datetime(result["time"], utc=True)
    if not result.time.is_monotonic_increasing or result.time.duplicated().any():
        raise ValueError("Candle times must be unique and increasing")
    prices = result[["open", "high", "low", "close"]]
    if not np.isfinite(prices.to_numpy()).all():
        raise ValueError("Candle prices must be finite")
    if ((result.high < prices.max(axis=1)) | (result.low > prices.min(axis=1))).any():
        raise ValueError("Inconsistent OHLC candle")
    result["close_time"] = result.time + pd.Timedelta(minutes=TF_MINUTES[timeframe])
    cutoff = pd.Timestamp.now(tz="UTC") if as_of is None else pd.Timestamp(as_of)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    return result.loc[result.close_time <= cutoff].reset_index(drop=True)


def simulate_daily_exit(ltf, entry_idx, entry, stop, target, direction, max_hold,
                        risk, invalidation_idx=None, breakeven_trigger_r=None,
                        breakeven_buffer_r=0.0):
    """Gap fills at open; invalidation next open; stop first on ambiguous bars.

Intrabar fills are timestamped at close so unknown proceeds cannot fund
another entry earlier in that candle. Breakeven activates next bar.
"""
    sign = 1 if direction == "bullish" else -1
    end = min(entry_idx + max_hold - 1, len(ltf) - 1)
    current_stop, armed, changes = stop, False, []
    for k in range(entry_idx, end + 1):
        row = ltf.iloc[k]
        phase = "open"
        if sign * (row.open - current_stop) <= 0:
            price, reason = row.open, "stop_gap"
        elif sign * (row.open - target) >= 0:
            price, reason = row.open, "target_gap"
        elif invalidation_idx is not None and k == invalidation_idx + 1:
            price, reason = row.open, "invalidation"
        else:
            phase = "close"
            adverse = row.low if sign == 1 else row.high
            favorable = row.high if sign == 1 else row.low
            if sign * (adverse - current_stop) <= 0:
                price, reason = current_stop, "stop"
            elif sign * (favorable - target) >= 0:
                price, reason = target, "target"
            elif k == end:
                price = row.close
                reason = "timeout" if entry_idx + max_hold - 1 <= len(ltf) - 1 else "data_end"
            else:
                if (breakeven_trigger_r is not None and not armed
                        and sign * (favorable - entry) >= breakeven_trigger_r * risk):
                    proposed = entry + sign * breakeven_buffer_r * risk
                    current_stop = max(current_stop, proposed) if sign == 1 else min(current_stop, proposed)
                    changes.append({"time": ltf.time.iloc[k + 1], "stop": current_stop})
                    armed = True
                continue
        r = sign * (price - entry) / risk
        return {"exit_idx": k, "exit_price": float(price), "exit_reason": reason,
                "exit_phase": phase, "exit_bar_time": row.time,
                "exit_time": row.time if phase == "open" else row.close_time,
                "r_multiple": float(r), "stop_changes": changes,
                "outcome": "win" if r > 0 else ("loss" if r < 0 else "breakeven")}
    raise ValueError("No executable entry candle")


def account_trades(trades, risk_pct, start_equity):
    """Size from realized cash, settling prior exits before simultaneous entries."""
    result = trades.copy().sort_values(["entry_time", "symbol", "chain_id", "leg_type"],
                                      kind="stable").reset_index(drop=True)
    for col in ("risk_amount", "pnl_amount", "balance_after", "balance_at_entry"):
        result[col] = 0.0
    events = {}
    for i, row in result.iterrows():
        events.setdefault(pd.Timestamp(row.entry_time), {"entry": [], "exit": []})["entry"].append(i)
        events.setdefault(pd.Timestamp(row.exit_time), {"entry": [], "exit": []})["exit"].append(i)
    balance = float(start_equity)
    curve, times = [balance], []
    for t, event in sorted(events.items()):
        prior = [i for i in event["exit"] if i not in event["entry"]]
        same = [i for i in event["exit"] if i in event["entry"]]
        if prior:
            balance += sum(result.at[i, "pnl_amount"] for i in prior)
            for i in prior:
                result.at[i, "balance_after"] = balance
            curve.append(balance)
            times.append(t)
        for i in event["entry"]:
            amount = max(balance, 0.0) * risk_pct / 100 * result.at[i, "risk_mult"]
            result.at[i, "balance_at_entry"] = balance
            result.at[i, "risk_amount"] = amount
            result.at[i, "pnl_amount"] = amount * result.at[i, "r_multiple"]
        if same:
            balance += sum(result.at[i, "pnl_amount"] for i in same)
            for i in same:
                result.at[i, "balance_after"] = balance
            curve.append(balance)
            times.append(t)
    values = np.asarray(curve)
    drawdowns = (values - np.maximum.accumulate(values)) / np.maximum.accumulate(values)
    profits = result.loc[result.pnl_amount > 0, "pnl_amount"].sum()
    losses = -result.loc[result.pnl_amount < 0, "pnl_amount"].sum()
    return result, {"equity_curve": curve, "equity_times": times,
                    "final_equity": balance, "max_drawdown_pct": float(drawdowns.min() * 100),
                    "profit_factor": float(profits / losses) if losses else None,
                    "accounting": "realized_cash", "drawdown_basis": "realized balance; excludes open P&L"}
