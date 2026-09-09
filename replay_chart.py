"""Candle-by-candle replay of one backtested trade, with play/pause/step controls.

Unlike the static annotated PNG, the sweep/MSS/OTE/entry/exit markers only
appear once the replay actually reaches that point in time -- it plays back
the setup the way it happened, bar by bar.
"""

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import MetaTrader5 as mt5

TF_MAP = {"M1": mt5.TIMEFRAME_M1, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}


def _nearest_idx(times: pd.Series, target: pd.Timestamp):
    if pd.isna(target):
        return None
    diffs = (times - target).abs()
    idx = diffs.idxmin()
    return int(idx)


def build_replay_figure(symbol: str, ltf_name: str, row: pd.Series):
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
    n = len(df)

    sweep_idx = _nearest_idx(df["time"], pd.Timestamp(row["sweep_time"])) if pd.notna(row.get("sweep_time")) else None
    mss_idx = _nearest_idx(df["time"], pd.Timestamp(row["mss_time"])) if pd.notna(row.get("mss_time")) else None
    entry_idx = _nearest_idx(df["time"], entry_time)
    exit_idx = _nearest_idx(df["time"], exit_time)

    direction = row["direction"]
    poi_color = "royalblue" if direction == "long" else "indianred"
    t0, t1 = df["time"].iloc[0], df["time"].iloc[-1]

    def build_shapes(k):
        shapes = []
        # HTF POI zone -- always visible, it predates the window
        if pd.notna(row.get("poi_top")) and pd.notna(row.get("poi_bottom")):
            shapes.append(dict(
                type="rect", xref="x", yref="y",
                x0=t0, x1=df["time"].iloc[k], y0=row["poi_bottom"], y1=row["poi_top"],
                fillcolor=poi_color, opacity=0.12, line=dict(color=poi_color, dash="dash", width=1)))
        # OTE zone -- appears once MSS has happened
        if mss_idx is not None and k >= mss_idx and pd.notna(row.get("ote_top")):
            shapes.append(dict(
                type="rect", xref="x", yref="y",
                x0=df["time"].iloc[mss_idx], x1=df["time"].iloc[min(k, n - 1)],
                y0=row["ote_bottom"], y1=row["ote_top"],
                fillcolor="orange", opacity=0.35, line=dict(color="darkorange", width=1)))
        # MSS vertical line
        if mss_idx is not None and k >= mss_idx:
            shapes.append(dict(type="line", xref="x", yref="paper",
                                x0=df["time"].iloc[mss_idx], x1=df["time"].iloc[mss_idx], y0=0, y1=1,
                                line=dict(color="gray", dash="dot", width=1)))
        # entry / stop / target lines -- appear once entered
        if entry_idx is not None and k >= entry_idx:
            for level, color in [(row["entry"], "black"), (row["stop"], "red"), (row["target"], "green")]:
                shapes.append(dict(type="line", xref="x", yref="y",
                                    x0=df["time"].iloc[entry_idx], x1=df["time"].iloc[min(k, n - 1)],
                                    y0=level, y1=level, line=dict(color=color, dash="dash", width=1.3)))
        return shapes

    def build_annotations(k):
        anns = []
        if pd.notna(row.get("poi_top")):
            anns.append(dict(x=t0, y=row["poi_top"], text=f"HTF POI ({row.get('poi_type','')})",
                              showarrow=False, xanchor="left", yanchor="bottom", font=dict(color=poi_color, size=11)))
        if sweep_idx is not None and k >= sweep_idx:
            anns.append(dict(x=df["time"].iloc[sweep_idx], y=row["sweep_extreme"], text="sweep (TS)",
                              showarrow=True, arrowhead=2, ay=30 if direction == "long" else -30,
                              font=dict(color="purple", size=11)))
        if mss_idx is not None and k >= mss_idx:
            anns.append(dict(x=df["time"].iloc[mss_idx], y=1, yref="paper", text="MSS/CISD",
                              showarrow=False, textangle=-90, font=dict(color="gray", size=11)))
        if entry_idx is not None and k >= entry_idx:
            anns.append(dict(x=df["time"].iloc[min(k, n - 1)], y=row["entry"], text="Entry",
                              showarrow=False, xanchor="left", font=dict(color="black", size=11)))
        if k >= n - 1 and exit_idx is not None:
            pass  # exit marker handled as a scatter point below
        return anns

    def candle_trace(k):
        d = df.iloc[:k + 1]
        return go.Candlestick(x=d["time"], open=d["open"], high=d["high"], low=d["low"], close=d["close"],
                               increasing_line_color="#26a69a", decreasing_line_color="#ef5350", name=symbol)

    def marker_traces(k):
        traces = []
        if entry_idx is not None and k >= entry_idx:
            marker = "triangle-up" if direction == "long" else "triangle-down"
            traces.append(go.Scatter(x=[df["time"].iloc[entry_idx]], y=[row["entry"]], mode="markers",
                                      marker=dict(symbol=marker, size=14, color="black"), name="Entry", showlegend=False))
        if exit_idx is not None and k >= exit_idx:
            color = "green" if row["outcome"] == "win" else "red"
            traces.append(go.Scatter(x=[df["time"].iloc[exit_idx]], y=[row["exit_price"]], mode="markers",
                                      marker=dict(symbol="star", size=16, color=color), name="Exit", showlegend=False))
        return traces

    frames = []
    for k in range(n):
        frames.append(go.Frame(
            data=[candle_trace(k), *marker_traces(k)],
            layout=go.Layout(shapes=build_shapes(k), annotations=build_annotations(k)),
            name=str(k),
        ))

    fig = go.Figure(
        data=[candle_trace(0)],
        layout=go.Layout(shapes=build_shapes(0), annotations=build_annotations(0)),
        frames=frames,
    )

    fig.update_layout(
        title=f"{symbol} {ltf_name} — {direction} ({row['outcome']}, {row['r_multiple']:.1f}R) — candle replay",
        xaxis_rangeslider_visible=False,
        height=650,
        margin=dict(t=60, b=40),
        updatemenus=[dict(
            type="buttons", showactive=False, x=0.0, y=1.12, xanchor="left",
            buttons=[
                dict(label="Play", method="animate",
                     args=[None, dict(frame=dict(duration=250, redraw=True), fromcurrent=True, transition=dict(duration=0))]),
                dict(label="Pause", method="animate",
                     args=[[None], dict(frame=dict(duration=0, redraw=False), mode="immediate")]),
            ],
        )],
        sliders=[dict(
            active=0, x=0.08, y=1.12, len=0.9, xanchor="left",
            currentvalue=dict(prefix="Bar: ", visible=True, xanchor="right"),
            steps=[dict(method="animate", label=str(k),
                        args=[[str(k)], dict(mode="immediate", frame=dict(duration=0, redraw=True), transition=dict(duration=0))])
                   for k in range(n)],
        )],
    )
    # fix axis ranges so the chart doesn't jump around as bars/shapes are added
    fig.update_xaxes(range=[t0, t1])
    y_lo = min(df["low"].min(), row.get("poi_bottom", df["low"].min()), row.get("stop", df["low"].min()))
    y_hi = max(df["high"].max(), row.get("poi_top", df["high"].max()), row.get("target", df["high"].max()))
    pad = (y_hi - y_lo) * 0.05
    fig.update_yaxes(range=[y_lo - pad, y_hi + pad])

    return fig
