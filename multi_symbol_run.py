"""
Run the HTF->LTF ICT backtest across multiple symbols over full available
history and report combined + per-symbol stats.
"""

import argparse
import pandas as pd
import numpy as np

from htf_ltf_backtest import run, DEFAULT_KILLZONES, HTF_TO_LTF


def combined_summary(trades: pd.DataFrame, risk_pct=1.0, start_equity=10_000):
    if trades.empty:
        print("No trades at all.")
        return None
    trades = trades.sort_values("entry_time").reset_index(drop=True)
    wins = trades[trades["r_multiple"] > 0]
    losses = trades[trades["r_multiple"] <= 0]
    win_rate = len(wins) / len(trades) * 100
    avg_r = trades["r_multiple"].mean()
    profit_factor = wins["r_multiple"].sum() / abs(losses["r_multiple"].sum()) if len(losses) else float("inf")

    risk_mults = trades["risk_mult"] if "risk_mult" in trades.columns else pd.Series([1.0] * len(trades))
    equity = [start_equity]
    for r, rm in zip(trades["r_multiple"], risk_mults):
        equity.append(equity[-1] * (1 + risk_pct / 100 * rm * r))
    equity = np.array(equity)
    dd = (equity - np.maximum.accumulate(equity)) / np.maximum.accumulate(equity)

    print("\n=== COMBINED (all symbols, chronological, shared account) ===")
    print(f"Trades:          {len(trades)}")
    print(f"Win rate:        {win_rate:.1f}%")
    print(f"Avg R:           {avg_r:.2f}")
    print(f"Profit factor:   {profit_factor:.2f}")
    print(f"Final equity:    {equity[-1]:,.2f} (start {start_equity:,.2f}, risk {risk_pct}%/trade)")
    print(f"Max drawdown:    {dd.min()*100:.1f}%")
    return equity, trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["EURUSD", "USDJPY", "XAUUSD"])
    ap.add_argument("--htf", default="H4", choices=HTF_TO_LTF.keys())
    ap.add_argument("--htf-bars", type=int, default=9000)
    ap.add_argument("--min-rr", type=float, default=3.0)
    ap.add_argument("--max-rr", type=float, default=5.0)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--pyramiding", action="store_true")
    ap.add_argument("--max-pyramid-legs", type=int, default=3)
    ap.add_argument("--pyramid-risk-mult", type=float, default=0.5)
    ap.add_argument("--out", default="multi_symbol_trades.csv")
    ap.add_argument("--plot", default="multi_symbol_equity.png")
    args = ap.parse_args()

    all_trades = []
    for symbol in args.symbols:
        print(f"\n--- {symbol} ({args.htf} -> {HTF_TO_LTF[args.htf]}, {args.htf_bars} HTF bars) ---")
        try:
            trades = run(symbol, args.htf, args.htf_bars, DEFAULT_KILLZONES,
                         min_rr=args.min_rr, max_rr=args.max_rr,
                         pyramiding=args.pyramiding, max_pyramid_legs=args.max_pyramid_legs,
                         pyramid_risk_mult=args.pyramid_risk_mult)
        except Exception as e:
            print(f"  FAILED: {e}")
            continue
        trades["symbol"] = symbol
        all_trades.append(trades)

        if not trades.empty:
            wins = trades[trades["r_multiple"] > 0]
            win_rate = len(wins) / len(trades) * 100
            avg_r = trades["r_multiple"].mean()
            print(f"  {symbol}: {len(trades)} trades, {win_rate:.1f}% win rate, avg {avg_r:.2f}R")
        else:
            print(f"  {symbol}: 0 trades")

    if not all_trades:
        print("No results for any symbol.")
        return

    combined = pd.concat(all_trades, ignore_index=True)
    combined.to_csv(args.out, index=False)
    print(f"\nCombined trade log saved to {args.out}")

    result = combined_summary(combined, risk_pct=args.risk_pct)
    if result is not None:
        equity, sorted_trades = result
        import matplotlib.pyplot as plt
        plt.figure(figsize=(11, 5))
        plt.plot(equity)
        plt.title(f"Combined ICT HTF/LTF strategy — {', '.join(args.symbols)} ({args.htf}->{HTF_TO_LTF[args.htf]})")
        plt.xlabel("Trade #")
        plt.ylabel("Equity")
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(args.plot)
        print(f"Combined equity curve saved to {args.plot}")


if __name__ == "__main__":
    main()
