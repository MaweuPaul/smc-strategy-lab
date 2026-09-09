# Daily Order Block Retracement

A daily-bias, H1-execution strategy: find the order block behind a strong D1
impulse, wait for price to retrace into it, enter on confirmation.

**Local research tool. Data is read-only from a running MetaTrader 5 terminal.
No orders are ever placed by any of this code.**

> **Documentation depth note:** like [Asian Session](../asian_session/README.md),
> this strategy has not had the same depth of experimentation as
> [Daily FVG](../daily_fvg/README.md). What follows documents the rule as coded
> and the numbers measured at its actual shipped API defaults.

---

> See the [main README](../README.md#strategies) for the table of contents
> across all strategies.

## What Is This?

For each Order Block (OB) formed by an impulsive D1 move, wait for price to
retrace back into that zone and enter, either immediately on touch, or on a
confirmed M15 Change of Character inside the zone, depending on
`entry_precision`. Both long and short setups are traded, in the direction of
the original impulse (or optionally filtered/reversed by D1 structure bias).
Supports an optional pyramid add and an optional structure-based invalidation
exit in place of a hard stop-only exit.

---

## Why This Might Work

An Order Block is read as the last candle of the *losing* side before a
strong displacement move, the theory holds that the aggressive orders which
fueled that displacement were resting there, not fully filled, and that a
later revisit to that exact candle's range finds the same unfilled interest
and continues the original move. It's the same "unfilled interest attracts a
revisit" logic as [Daily FVG](../daily_fvg/README.md#why-this-might-work),
just anchored to a specific candle rather than a 3-candle gap, and traded in
**both directions** here (a bullish OB behind an up-impulse, a bearish OB
behind a down-impulse), rather than long-only.

`bias_mode="reverse"` tests a competing idea directly: instead of assuming
the OB still holds its original-direction interest, treat it as a *breaker*, a level that failed once and is more useful faded than trusted, once
structure disagrees with the original impulse.

This hasn't had a Daily-FVG-depth audit yet (no bearish-mirror-style control test,
no look-ahead-bias pass, no comparison of `bias_mode` variants against each
other), the [Backtesting & Results](#backtesting--results) numbers below are
the shipped rule's performance, not a test of which piece of the theory is
actually doing the work. Worth noting: unlike Daily FVG, three of six symbols
already lose money at the shipped defaults, which is itself a hint that this
version of the "unfilled interest" theory doesn't transfer as cleanly to
every instrument.

---

## Strategy Logic

```
┌─────────────────────────────────────────────────────────────────┐
│                 DAILY ORDER BLOCK : TRADE LOGIC                  │
└─────────────────────────────────────────────────────────────────┘

  STEP 1: FIND THE IMPULSE (D1, or H4 if htf_timeframe="H4")
  ┌──────────────────────────────────────┐
  │  A confirmed swing breaks, with a    │
  │  strong displacement move away from  │
  │  it (optionally requiring a FVG in   │
  │  the impulse leg -- require_         │
  │  displacement)                       │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 2: MARK THE ORDER BLOCK
  ┌──────────────────────────────────────┐
  │  Last opposing candle before that    │
  │  impulse = the OB zone               │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 3: WAIT FOR THE RETRACEMENT
  ┌──────────────────────────────────────┐
  │  entry_precision="h1" (default):     │
  │    enter on the first H1 touch       │
  │  entry_precision="m15_choch":        │
  │    wait for an M15 CHoCH after the   │
  │    touch, tighter stop at that       │
  │    retracement's actual extreme      │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 4: BIAS CHECK (optional, bias_mode)
  ┌──────────────────────────────────────┐
  │  "off"    : always trade the OB's    │
  │             original direction       │
  │  "filter" : skip if D1 structure     │
  │             (HH+HL/LH+LL) disagrees  │
  │  "reverse": flip to fade the zone as │
  │             a breaker block instead  │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 5: STOP / TARGET / MANAGEMENT
  ┌──────────────────────────────────────┐
  │  Stop: beyond the OB zone (or the    │
  │  m15_choch retracement extreme)      │
  │  Target: RR range (default 3-5R)     │
  │  Optional: pyramid add at N-R,       │
  │  stop = base entry (locked to BE)    │
  │  Optional: invalidation exit if      │
  │  structure invalidates the zone,     │
  │  instead of riding to hard stop      │
  └───────────────────────────────────────┘
```

---

## System Components

| File | Role |
|---|---|
| `daily_ob_backtest.py` | `run_daily_ob()`, OB detection, retracement entry, bias modes, pyramid, invalidation exit |
| `daily_ob_support.py` | `account_trades()`, `TF_MINUTES`, shared accounting/timeframe helpers |
| `discount_breakout_backtest.py` | `compute_confirmed_swings()`, shared swing detector |
| `backend_api.py` | `POST /api/daily-ob-backtest` |
| `frontend/src/DailyObPanel.jsx` | Settings sidebar, trade log, Monte Carlo (shares `MonteCarlo.jsx` with Daily FVG), chart |

---

## Settings Reference

`POST /api/daily-ob-backtest`

| Parameter | Default | Description |
|---|---|---|
| `symbols` | - | required, list |
| `htf_timeframe` | `D1` | or `H4`, timeframe the impulse/OB is found on |
| `htf_bars` | 2600 | |
| `swing_window` | 3 | Fractal swing confirmation window |
| `min_rr` / `max_rr` | 3.0 / 5.0 | Target reward:risk range |
| `use_killzones` | false | Restrict entries to London/NY hours |
| `bias_mode` | `off` | `off` \| `filter` \| `reverse`, D1 structure agreement |
| `require_displacement` | true | Require a FVG in the impulse leg before it counts |
| `use_invalidation_exit` | true | Exit on structure invalidation instead of riding to the hard stop |
| `invalidation_minor_window` / `invalidation_confirm_bars` / `invalidation_grace_bars` | 2 / 1 / 10 | Invalidation-exit tuning |
| `pyramid_trigger_r` | none | If set, add a second leg once the base trade reaches this many R |
| `pyramid_risk_mult` | 0.5 | Size of the pyramid add relative to normal risk |
| `risk_pct` | 1.0 | |
| `start_equity` | 10000 | |

(`entry_precision`, `"h1"` or `"m15_choch"`, exists in `run_daily_ob()` but
is **not** currently exposed in the API request model; the endpoint always
uses the `"h1"` default.)

---

## Backtesting & Results

Measured at the API's actual shipped defaults (D1, RR 3–5, `require_displacement=True`,
`use_invalidation_exit=True`, `bias_mode="off"`):

| Symbol | n | Win% | avgR | PF |
|---|---|---|---|---|
| US30 | 107 | 33.6% | +0.111 | 1.26 |
| GBPUSD | 113 | 34.5% | +0.124 | 1.27 |
| XAUUSD | 113 | 32.7% | +0.049 | 1.12 |
| EURUSD | 116 | 32.8% | -0.134 | 0.72 |
| USDJPY | 115 | 32.2% | -0.171 | 0.66 |
| USTEC | 118 | 30.5% | -0.275 | 0.46 |

GBPUSD, US30 and XAUUSD show a real edge at these settings; EURUSD, USDJPY
and USTEC currently lose money. **None of `bias_mode`, `entry_precision`,
pyramiding, or the killzone filter have been swept**, this is the as-shipped
baseline, not a searched-for optimum.

---

## Known Limitations

- Same MT5 intraday-history caveat as the other strategies, H1 execution
  data only reaches back to where the local cache is dense.
- No look-ahead-bias audit trail like the Daily FVG family has.
- `entry_precision="m15_choch"` (the tighter-stop entry variant) exists in the
  code but isn't reachable from the API/panel, untested from this app.
- No Monte Carlo or random-subsample validation on any of its filters.

---

## Testing It

```python
from daily_ob_backtest import run_daily_ob
trades = run_daily_ob("XAUUSD", htf_bars=2600, swing_window=3, min_rr=3.0, max_rr=5.0,
                       require_displacement=True, use_invalidation_exit=True)
print(trades.r_multiple.mean(), len(trades))
```

or via the API:

```python
import requests
r = requests.post("http://localhost:8001/api/daily-ob-backtest", json={
    "symbols": ["XAUUSD"], "min_rr": 3.0, "max_rr": 5.0,
})
print(r.json()["metrics"])
```
