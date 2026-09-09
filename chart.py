"""Render one backtested trade as an annotated candlestick chart (works for any trade row)."""

from datetime import timedelta

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import MetaTrader5 as mt5

TF_MAP = {"M1": mt5.TIMEFRAME_M1, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}


def plot_trade(symbol: str, ltf_name: str, row: pd.Series):
    mt5.initialize()
    entry_time = pd.Timestamp(row["entry_time"])
    exit_time = pd.Timestamp(row["exit_time"])
    window_start = pd.Timestamp(row["window_start_time"]) if pd.notna(row.get("window_start_time")) else entry_time - timedelta(hours=24)

    start = window_start - timedelta(hours=8)
    end = exit_time + timedelta(hours=8)
    rates = mt5.copy_rates_range(symbol, TF_MAP[ltf_name], start, end)
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    if df.empty:
        raise RuntimeError("No price data returned for this trade's time window")

    fig, ax = plt.subplots(figsize=(12, 7))
    bar_minutes = {"M1": 0.7, "M15": 10, "H1": 40, "H4": 160}[ltf_name]
    width = timedelta(minutes=bar_minutes)
    for _, r in df.iterrows():
        color = "#26a69a" if r["close"] >= r["open"] else "#ef5350"
        ax.plot([r["time"], r["time"]], [r["low"], r["high"]], color=color, linewidth=1)
        ax.add_patch(Rectangle(
            (mdates.date2num(r["time"] - width / 2), min(r["open"], r["close"])),
            mdates.date2num(r["time"] + width / 2) - mdates.date2num(r["time"] - width / 2),
            max(abs(r["close"] - r["open"]), 1e-6),
            facecolor=color, edgecolor=color))

    t0, t1 = df["time"].iloc[0], df["time"].iloc[-1]
    direction = row["direction"]
    poi_color = "tab:blue" if direction == "long" else "tab:red"

    if pd.notna(row.get("poi_top")) and pd.notna(row.get("poi_bottom")):
        ax.add_patch(Rectangle(
            (mdates.date2num(t0), row["poi_bottom"]),
            mdates.date2num(t1) - mdates.date2num(t0),
            row["poi_top"] - row["poi_bottom"],
            facecolor=poi_color, alpha=0.12, edgecolor=poi_color, linestyle="--"))
        ax.text(t0, row["poi_top"], f"  HTF POI ({row.get('poi_type', '')})", color=poi_color, va="bottom", fontsize=9)

    if pd.notna(row.get("ote_top")) and pd.notna(row.get("ote_bottom")) and pd.notna(row.get("mss_time")):
        mss_t = pd.Timestamp(row["mss_time"])
        ax.add_patch(Rectangle(
            (mdates.date2num(mss_t), row["ote_bottom"]),
            max(mdates.date2num(entry_time) - mdates.date2num(mss_t), 0.02),
            row["ote_top"] - row["ote_bottom"],
            facecolor="orange", alpha=0.35, edgecolor="darkorange"))
        ax.text(mss_t, row["ote_top"], "  OTE (61.8-79%)", color="darkorange", va="bottom", fontsize=9)
        ax.axvline(mss_t, color="gray", linestyle=":", linewidth=1)
        ax.text(mss_t, df["high"].max(), "MSS/CISD", color="gray", fontsize=9, rotation=90, va="top")

    if pd.notna(row.get("sweep_time")) and pd.notna(row.get("sweep_extreme")):
        sweep_t = pd.Timestamp(row["sweep_time"])
        marker = "v" if direction == "long" else "^"
        ax.scatter([sweep_t], [row["sweep_extreme"]], color="purple", zorder=5, s=60, marker=marker)
        ax.text(sweep_t, row["sweep_extreme"], "sweep (TS)", color="purple", fontsize=9, ha="center",
                va="top" if direction == "long" else "bottom")

    ax.axhline(row["entry"], color="black", linewidth=1)
    ax.text(t1, row["entry"], "  Entry", va="center", fontsize=9)
    ax.axhline(row["stop"], color="red", linewidth=1, linestyle="--")
    ax.text(t1, row["stop"], "  Stop", va="center", fontsize=9, color="red")
    ax.axhline(row["target"], color="green", linewidth=1, linestyle="--")
    rr = row.get("rr_planned", 0) or 0
    ax.text(t1, row["target"], f"  Target ({rr:.1f}R)", va="center", fontsize=9, color="green")

    ax.scatter([entry_time], [row["entry"]], color="black", zorder=5,
               marker="^" if direction == "long" else "v", s=80)
    exit_color = "green" if row["outcome"] == "win" else "red"
    ax.scatter([exit_time], [row["exit_price"]], color=exit_color, zorder=5, marker="*", s=150)

    ax.set_title(f"{symbol} {ltf_name} — {direction} ({row['outcome']}, {row['r_multiple']:.1f}R)\n"
                 "HTF POI touch -> LTF sweep -> MSS -> OTE pullback -> entry")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    ax.set_ylabel("Price")
    ax.grid(alpha=0.2)
    plt.tight_layout()
    return fig
