"""
Ad hoc comparison: does pyramid_risk_mult=0.5 (the dashboard default) hold
up once added to the headline Gold config on the FIXED daily_ob_backtest
engine (post look-ahead-bug fixes)?

Baseline (no pyramid) reproduces the earlier post-fix headline result:
113 trades, 34.5% win rate, avg R 0.12, PF 1.3, -7.4% max DD, $11,331 final.

Pyramid variant adds a second, half-risk leg once the base trade reaches
pyramid_trigger_r=1.1 (dashboard default), stop at base entry (breakeven),
same target as the base -- riding a de-risked add on the same thesis.
"""
import pandas as pd
from daily_ob_backtest import run_daily_ob
from daily_ob_support import account_trades

SYMBOL = "XAUUSD"
RISK_PCT = 1.0
START_EQUITY = 10_000.0

BASE_KWARGS = dict(
    swing_window=3, min_rr=3.0, max_rr=5.0,
    require_displacement=True, use_invalidation_exit=True,
    bias_mode="off",
)


def run(label, **overrides):
    kwargs = {**BASE_KWARGS, **overrides}
    trades = run_daily_ob(SYMBOL, **kwargs)
    if trades.empty:
        print(f"\n--- {label} ---\nNo trades.")
        return
    combined, ledger = account_trades(trades, RISK_PCT, START_EQUITY)
    n = len(combined)
    win_rate = (combined.r_multiple > 0).mean() * 100
    avg_r = combined.r_multiple.mean()
    print(f"\n--- {label} ---")
    print(f"  trades           : {n}")
    print(f"  win_rate         : {win_rate:.1f}%")
    print(f"  avg_r            : {avg_r:.3f}")
    print(f"  profit_factor    : {ledger['profit_factor']}")
    print(f"  max_drawdown_pct : {ledger['max_drawdown_pct']:.2f}%")
    print(f"  final_equity     : ${ledger['final_equity']:,.0f}")
    if "leg_type" in combined.columns:
        base_n = (combined.leg_type == "base").sum()
        pyr_n = (combined.leg_type == "pyramid").sum()
        print(f"  base legs        : {base_n}")
        print(f"  pyramid legs     : {pyr_n}")
        if pyr_n:
            pyr = combined[combined.leg_type == "pyramid"]
            print(f"  pyramid win_rate : {(pyr.r_multiple > 0).mean()*100:.1f}%")
            print(f"  pyramid avg_r    : {pyr.r_multiple.mean():.3f}")


if __name__ == "__main__":
    run("Baseline (no pyramid)", pyramid_trigger_r=None)
    run("Pyramid ON, trigger=1.1R, risk_mult=0.5", pyramid_trigger_r=1.1, pyramid_risk_mult=0.5)
