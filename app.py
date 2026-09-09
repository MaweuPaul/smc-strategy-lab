"""Interactive dashboard for the ICT HTF->LTF liquidity-sweep backtest.

Run with:  streamlit run app.py
Reads real history from your local MT5 terminal (read-only, no orders placed).
"""

import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

from htf_ltf_backtest import run, HTF_TO_LTF
from chart import plot_trade
from replay_chart import build_replay_figure

st.set_page_config(page_title="ICT HTF/LTF Backtest", layout="wide")
st.title("ICT / SMC — HTF bias -> LTF confirmation backtest")
st.caption("HTF POI touch -> LTF liquidity sweep -> MSS/CISD -> OTE pullback -> entry, 1:3-1:5 RR, "
           "London/NY killzones only. Data pulled live from your MT5 terminal (read-only).")

ALL_SYMBOLS = ["EURUSD", "USDJPY", "XAUUSD", "GBPUSD", "AUDUSD", "USDCHF", "NZDUSD", "USDCAD",
               "XAGUSD", "USTEC", "US30", "US500", "UK100"]

with st.sidebar:
    st.header("Settings")
    symbols = st.multiselect("Symbols", ALL_SYMBOLS, default=["EURUSD", "USDJPY", "XAUUSD"])
    st.caption("Note: on this broker, NAS100 = **USTEC**, Silver = **XAGUSD**, Gold = **XAUUSD**.")
    htf = st.selectbox("HTF", list(HTF_TO_LTF.keys()), index=1)
    st.caption(f"LTF used for confirmation: **{HTF_TO_LTF[htf]}**")
    htf_bars = st.slider("HTF bars (history depth)", 500, 9000, 3000, step=500)
    min_rr = st.slider("Min RR to use a structural target", 1.0, 5.0, 3.0, step=0.5)
    max_rr = st.slider("Max RR cap", 1.0, 8.0, 5.0, step=0.5)
    risk_pct = st.slider("Risk % per trade", 0.25, 5.0, 1.0, step=0.25)

    st.subheader("Killzones (UTC hours)")
    london = st.slider("London killzone", 0, 23, (7, 10))
    ny = st.slider("New York killzone", 0, 23, (12, 15))
    killzones = [london, ny]

    st.subheader("Pyramiding")
    pyramiding = st.checkbox("Add on BOS + fresh FVG continuation zone", value=False)
    st.caption("After the base entry, watch the LTF for a break of structure that leaves behind a fresh "
               "same-direction FVG (the broken level is the liquidity left behind; the FVG next to it is "
               "the zone). When price retraces into that zone, add a reduced-risk leg. Repeats up to the cap below.")
    max_pyramid_legs = st.slider("Max legs (base + adds)", 1, 5, 3, disabled=not pyramiding)
    pyramid_risk_mult = st.slider("Risk multiplier per additional leg", 0.1, 1.0, 0.5, step=0.1,
                                   disabled=not pyramiding,
                                   help="Leg 2 risks this fraction of normal risk, leg 3 risks this fraction squared, etc.")

    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

if "results" not in st.session_state:
    st.session_state.results = None

if run_btn:
    if not symbols:
        st.warning("Pick at least one symbol.")
    else:
        all_trades = []
        progress = st.progress(0.0, text="Running backtest...")
        for i, sym in enumerate(symbols):
            progress.progress(i / len(symbols), text=f"Running {sym}...")
            try:
                trades = run(sym, htf, htf_bars, killzones, min_rr=min_rr, max_rr=max_rr,
                             pyramiding=pyramiding, max_pyramid_legs=max_pyramid_legs,
                             pyramid_risk_mult=pyramid_risk_mult)
                trades["symbol"] = sym
                all_trades.append(trades)
            except Exception as e:
                st.warning(f"{sym} failed: {e}")
        progress.empty()
        combined = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
        st.session_state.results = {"trades": combined, "htf": htf, "risk_pct": risk_pct}

results = st.session_state.results

if results is None:
    st.info("Configure settings in the sidebar and click **Run backtest**.")
elif results["trades"].empty:
    st.info("No trades passed HTF -> LTF -> killzone confirmation with these settings. Try more HTF bars or wider killzones.")
else:
    trades = results["trades"].sort_values("entry_time").reset_index(drop=True)
    wins = trades[trades.r_multiple > 0]
    losses = trades[trades.r_multiple <= 0]
    win_rate = len(wins) / len(trades) * 100
    avg_r = trades.r_multiple.mean()
    pf = wins.r_multiple.sum() / abs(losses.r_multiple.sum()) if len(losses) else float("inf")

    risk_mults = trades["risk_mult"] if "risk_mult" in trades.columns else pd.Series([1.0] * len(trades))
    equity = [10_000.0]
    for r, rm in zip(trades.r_multiple, risk_mults):
        equity.append(equity[-1] * (1 + results["risk_pct"] / 100 * rm * r))
    equity = np.array(equity)
    dd = (equity - np.maximum.accumulate(equity)) / np.maximum.accumulate(equity)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Trades", len(trades))
    c2.metric("Win rate", f"{win_rate:.1f}%")
    c3.metric("Avg R", f"{avg_r:.2f}")
    c4.metric("Profit factor", f"{pf:.2f}" if pf != float("inf") else "inf")
    c5.metric("Max drawdown", f"{dd.min()*100:.1f}%")

    fig, ax = plt.subplots(figsize=(11, 3))
    ax.plot(equity)
    ax.set_title("Equity curve")
    ax.set_xlabel("Trade #")
    ax.grid(alpha=0.3)
    st.pyplot(fig)
    plt.close(fig)

    st.subheader("Per-symbol breakdown")
    breakdown = trades.groupby("symbol").agg(
        trades=("r_multiple", "count"),
        win_rate=("r_multiple", lambda s: (s > 0).mean() * 100),
        avg_r=("r_multiple", "mean"),
    ).round(2)
    st.dataframe(breakdown, use_container_width=True)

    st.subheader("Trade log")
    display_cols = ["symbol", "direction", "leg_type", "entry_time", "exit_time", "entry", "stop",
                     "target", "outcome", "r_multiple", "risk_mult"]
    display_cols = [c for c in display_cols if c in trades.columns]
    st.dataframe(trades[display_cols], use_container_width=True, height=300)

    st.subheader("Inspect a trade")
    options = [f"{i}" for i in trades.index]
    labels = {
        f"{i}": f"{i}: {r.symbol} {r.direction} {r.entry_time} ({r.outcome}, {r.r_multiple:.1f}R)"
        for i, r in trades.iterrows()
    }
    choice = st.selectbox("Pick a trade to visualize", options, format_func=lambda k: labels[k])
    view_mode = st.radio("View", ["Candle replay", "Static annotated chart"], horizontal=True)
    if choice is not None:
        row = trades.iloc[int(choice)]
        ltf_name = HTF_TO_LTF[results["htf"]]
        if view_mode == "Candle replay":
            try:
                fig2 = build_replay_figure(row["symbol"], ltf_name, row)
                st.plotly_chart(fig2, use_container_width=True)
                st.caption("Hit Play, or drag the Bar slider, to watch the setup unfold candle by candle. "
                           "Sweep/MSS/OTE/entry/exit markers only appear once the replay reaches that bar.")
            except Exception as e:
                st.error(f"Couldn't render replay: {e}")
        else:
            try:
                fig2 = plot_trade(row["symbol"], ltf_name, row)
                st.pyplot(fig2)
                plt.close(fig2)
            except Exception as e:
                st.error(f"Couldn't render chart: {e}")

    st.download_button("Download trade log (CSV)", trades.to_csv(index=False), "trades.csv", "text/csv")
