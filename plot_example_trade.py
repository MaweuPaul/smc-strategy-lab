"""Plot one real backtested trade with all the ICT levels annotated, like a TradingView chart."""

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import MetaTrader5 as mt5
from datetime import timedelta

SYMBOL = "USDJPY"
ROW = {
    "poi_top": 154.813, "poi_bottom": 154.588,
    "window_start_time": "2025-11-17 12:00:00+00:00",
    "sweep_time": "2025-11-18 04:15:00+00:00", "sweep_extreme": 154.811,
    "mss_time": "2025-11-18 04:30:00+00:00",
    "ote_top": 154.91987, "ote_bottom": 154.87085,
    "entry_time": "2025-11-18 07:15:00+00:00", "entry": 154.98,
    "stop": 154.806098, "target": 155.84951,
    "exit_time": "2025-11-19 09:30:00+00:00", "exit_price": 155.84951,
}

mt5.initialize()
start = pd.Timestamp(ROW["window_start_time"]) - timedelta(hours=8)
end = pd.Timestamp(ROW["exit_time"]) + timedelta(hours=8)
rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M15, start, end)
df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)

fig, ax = plt.subplots(figsize=(14, 8))

width = timedelta(minutes=10)
for _, r in df.iterrows():
    color = "#26a69a" if r["close"] >= r["open"] else "#ef5350"
    ax.plot([r["time"], r["time"]], [r["low"], r["high"]], color=color, linewidth=1)
    ax.add_patch(Rectangle(
        (mdates.date2num(r["time"] - width / 2), min(r["open"], r["close"])),
        mdates.date2num(r["time"] + width / 2) - mdates.date2num(r["time"] - width / 2),
        max(abs(r["close"] - r["open"]), 0.003),
        facecolor=color, edgecolor=color))

t0, t1 = df["time"].iloc[0], df["time"].iloc[-1]

# HTF POI zone (order block / FVG)
ax.add_patch(Rectangle((mdates.date2num(t0), ROW["poi_bottom"]),
                        mdates.date2num(t1) - mdates.date2num(t0),
                        ROW["poi_top"] - ROW["poi_bottom"],
                        facecolor="tab:blue", alpha=0.12, edgecolor="tab:blue", linestyle="--"))
ax.text(t0, ROW["poi_top"], "  HTF POI (bullish OB/FVG)", color="tab:blue", va="bottom", fontsize=9)

# OTE zone
ax.add_patch(Rectangle((mdates.date2num(pd.Timestamp(ROW["mss_time"])), ROW["ote_bottom"]),
                        mdates.date2num(pd.Timestamp(ROW["entry_time"])) - mdates.date2num(pd.Timestamp(ROW["mss_time"])) + 0.02,
                        ROW["ote_top"] - ROW["ote_bottom"],
                        facecolor="orange", alpha=0.35, edgecolor="darkorange"))
ax.text(pd.Timestamp(ROW["mss_time"]), ROW["ote_top"], "  OTE (61.8-79%)", color="darkorange", va="bottom", fontsize=9)

# sweep marker
ax.scatter([pd.Timestamp(ROW["sweep_time"])], [ROW["sweep_extreme"]], color="purple", zorder=5, s=60, marker="v")
ax.text(pd.Timestamp(ROW["sweep_time"]), ROW["sweep_extreme"] - 0.03, "sweep (TS)", color="purple", fontsize=9, ha="center")

# MSS marker
ax.axvline(pd.Timestamp(ROW["mss_time"]), color="gray", linestyle=":", linewidth=1)
ax.text(pd.Timestamp(ROW["mss_time"]), df["high"].max(), "MSS/CISD", color="gray", fontsize=9, rotation=90, va="top")

# entry / stop / target lines
ax.axhline(ROW["entry"], color="black", linewidth=1)
ax.text(t1, ROW["entry"], "  Entry", va="center", fontsize=9)
ax.axhline(ROW["stop"], color="red", linewidth=1, linestyle="--")
ax.text(t1, ROW["stop"], "  Stop", va="center", fontsize=9, color="red")
ax.axhline(ROW["target"], color="green", linewidth=1, linestyle="--")
ax.text(t1, ROW["target"], "  Target (5R)", va="center", fontsize=9, color="green")

ax.scatter([pd.Timestamp(ROW["entry_time"])], [ROW["entry"]], color="black", zorder=5, marker="^", s=80)
ax.scatter([pd.Timestamp(ROW["exit_time"])], [ROW["exit_price"]], color="green", zorder=5, marker="*", s=150)

ax.set_title(f"{SYMBOL} M15 — example backtested trade (long, +5R)\n"
             "HTF POI touch -> LTF liquidity sweep -> MSS -> OTE pullback -> entry")
ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
fig.autofmt_xdate()
ax.set_ylabel("Price")
ax.grid(alpha=0.2)
plt.tight_layout()
plt.savefig("example_trade.png", dpi=140)
print("saved example_trade.png")
