# ORB: New York Open

A two-sided (long/short) breakout strategy: mark the M1 candle(s) immediately
before the 9:30am New York open, then trade the first close beyond that
range's high or low.

**Local research tool. Data is read-only from a running MetaTrader 5 terminal.
No orders are ever placed by any of this code.**

---

> See the [main README](../README.md#strategies) for the table of contents
> across all strategies.

## What Is This?

For each NY trading day: mark the high/low of the M1 candle(s) ending exactly
at 9:30am New York time (either the single 9:29–9:30 candle, or the wider
9:25–9:30 5-minute window), then watch for the first M1 close beyond either
side after the open. Break above → buy. Break below → sell. Stop at the
opposite side of that marking range; target is either a multiple of the
range's own width or a fixed RR. One trade per day.

This is a **different rule** from an earlier, now-removed session-open ORB
version (see the main [README § Removed strategies](../README.md#removed-strategies)),
which marked a range for N minutes *after* a fixed UTC hour. Two things
matter here specifically because the anchor is "New York open":

- **9:30am NY is DST-dependent** (13:30 UTC in winter/EST, 14:30 UTC in
  summer/EDT). A fixed UTC hour would be wrong on one side of the clock change
  every year, this implementation computes the boundary in real
  `America/New_York` local time via Python's `zoneinfo`, verified correct
  across the March/November DST transitions.
- **The marking window is BEFORE the open, not after it**, the thesis is "did
  price take out the level printed into the open," not "did it break the
  first N minutes of the session."

---

## Why This Might Work

The idea behind any opening-range breakout is that the period right before
a major session open reflects thin, cautious positioning, nobody wants to
commit size ahead of the volume that's about to arrive. When that volume
actually shows up at the open, it tends to resolve the indecision in one
direction fairly quickly, and that initial thrust is thought to carry some
follow-through. Marking the range *before* 9:30 and trading the break of it
right at the open is a bet on catching that resolution early, rather than
waiting to see how the first few minutes of full liquidity play out (which
is what the older, removed session-open version did instead).

Worth being precise about what this backtest actually uses to make that
bet: it's price only. MT5 forex/CFD feeds carry `tick_volume` (a count of
price updates, not real traded volume), and this strategy doesn't reference
it at all, "the volume that arrives at the open" is the justification for
*why* a breakout might happen here, not something the code measures or
requires. The 9:30-anchored range and the daily bias filter (does today's
range agree with yesterday's) are the only two levers actually available to
separate a real thrust from noise, and per
[Backtesting & Results](#backtesting--results), the bias filter is the one
that's actually earned its keep so far, the win-rate-vs-profit-factor trap
below suggests plenty of "breakouts" here are exactly the noise this theory
predicts, not real follow-through.

---

## Strategy Logic

```
┌─────────────────────────────────────────────────────────────────┐
│                ORB (NY OPEN) : DAILY TRADE LOGIC (M1)             │
└─────────────────────────────────────────────────────────────────┘

  STEP 1: MARK THE RANGE (before the open)
  ┌──────────────────────────────────────┐
  │  candle_minutes=1: the single M1 bar │
  │    from 9:29-9:30 NY                 │
  │  candle_minutes=5: the M1 bars from  │
  │    9:25-9:30 NY (default -- tested   │
  │    better on every symbol)           │
  │  9:30 computed via real NY timezone, │
  │  correct across DST                  │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 2: WATCH FOR THE BREAKOUT (after 9:30)
  ┌──────────────────────────────────────┐
  │  First M1 CLOSE beyond either side   │
  │  of the range, within monitor_hours  │
  │  (default 6)                         │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 3: OPTIONAL DAILY BIAS FILTER
  ┌──────────────────────────────────────┐
  │  Only take the breakout if today's   │
  │  range midpoint > (or <) yesterday's │
  │  -- on by default, roughly halves    │
  │  trade count but improved every      │
  │  tested symbol                       │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 4: ENTER AT NEXT BAR'S OPEN
  ┌──────────────────────────────────────┐
  │  Stop: the OPPOSITE side of the      │
  │  marking range                       │
  │  Target: RR-based, OR a multiple of  │
  │  the range's own width               │
  │  ("measured_move" x1 -- the shipped  │
  │  default)                            │
  │  One trade per day (first valid      │
  │  breakout only)                      │
  └───────────────────────────────────────┘
```

---

## System Components

| File | Role |
|---|---|
| `orb_backtest.py` | `run_orb_ny_open()`, the whole strategy. Uses `fetch_m1()` (own module) plus `resolve_target()`/`simulate_exit()`/`summarize()` shared from `htf_ltf_backtest.py` |
| `backend_api.py` | `POST /api/orb-ny-open-backtest` |
| `frontend/src/OrbNyOpenPanel.jsx` | Settings sidebar, metrics, per-symbol/day-of-week/month breakdowns, trade log, candle replay |

---

## Settings Reference

`POST /api/orb-ny-open-backtest`

| Parameter | Default | Description |
|---|---|---|
| `symbols` | - | required, list |
| `bars` | 700000 | M1 bars to fetch per symbol |
| `candle_minutes` | 5 | 1 or 5, size of the marking window before 9:30 NY |
| `monitor_hours` | 6.0 | How long after the open to watch for a breakout |
| `target_mode` | `measured_move` | or `rr` |
| `target_range_mult` | 1.0 | Target = this × the marking range's width (measured-move mode) |
| `min_rr` / `max_rr` | 1.5 / 3.0 | Target reward:risk range (rr mode) |
| `max_hold_bars` | 4320 | M1 bars (3 days) before a trade is force-closed |
| `use_daily_bias` | true | Only take breakouts agreeing with today's range midpoint vs. yesterday's |
| `risk_pct` | 1.0 | |
| `start_equity` | 10000 | |

---

## Backtesting & Results

### 5-minute candle, measured-move x1, daily bias ON (the shipped default)

| Symbol | n | Win% | avgR | PF |
|---|---|---|---|---|
| XAUUSD | 265 | 62.6% | +0.122 | **1.33** |
| USTEC | 233 | 65.2% | +0.064 | 1.18 |
| US30 | 274 | 63.5% | +0.029 | 1.08 |
| GBPUSD | 255 | 55.3% | +0.003 | 1.01 |
| USDJPY | 265 | 54.0% | -0.023 | 0.95 |
| EURUSD | 254 | 50.0% | -0.091 | 0.82 |

Gold is the clear standout. 4 of 6 symbols sit at or above breakeven; EURUSD
and USDJPY currently lose money at this config.

### Levers tested

- **1-minute vs 5-minute marking candle**: 5-minute wins on every symbol
  tested. On the same 3-symbol combination (XAUUSD/USTEC/US30, bias on):
  5-minute gives a combined PF ≈ 1.1–1.3 per symbol; 1-minute gives **PF 0.97
  combined, a net loss** ($10,000 → $8,947 over the test window), despite a
  higher headline win rate (65.2%). The 1-minute range is narrow enough that
  most "breakouts" are noise: frequent small wins that don't cover the
  occasional full-stop loss. See the win-rate-vs-profitability point below, this is the same trap as Daily FVG's RR lever.
- **Daily bias filter**: on by default. Roughly halves trade count but
  improved profit factor on every symbol tested (e.g. Gold PF 1.00 → 1.33,
  USTEC PF 0.76 → 1.18), same "keep fewer, better trades" pattern seen in
  Daily FVG's H4 structure filter.
- **Target width** (measured-move x1 vs x2, or RR-based 1.5–3 / 2–4): x1 is
  the default; wider targets and RR-mode were tried and land close to
  breakeven-to-marginal on the symbols checked (Gold: PF 1.06–1.28 across
  those variants, never clearly beating x1's 1.33). Not swept as thoroughly as
  the levers above, worth revisiting.

### Win rate is not the story here: read it with profit factor

A 60–67% win rate looks strong on its own, but multiple configs above hit
that exact range while landing anywhere from a clear loss (1-minute, PF 0.97)
to a real edge (5-minute, PF 1.33). A tight target hits often almost by
construction; whether those frequent small wins outweigh the occasional full
stop-out is a separate question that only profit factor and final equity
answer. Always read win rate together with PF/avg R, never alone, this
project has hit that trap twice now (here and in
[Daily FVG](../daily_fvg/README.md#judging-a-result)'s RR lever).

### Day-of-week / month breakdowns

The panel groups every trade by entry weekday and calendar month, since a
strategy's aggregate profitability can hide a specific day or month that's
consistently dragging it down (or carrying it). On Gold at the default config,
for example, Friday and April have shown up red while Monday and Wednesday
carry most of the edge, with the sample sizes here (~50 trades per weekday
bucket, ~20 per month bucket), treat a single red cell as something to keep an
eye on, not yet a rule to trade around.

---

## Known Limitations

- **Same MT5 intraday-history caveat as every other strategy in this app**, M1 history only reaches back to where the local terminal cache is dense; see
  [Daily FVG § Known Limitations](../daily_fvg/README.md#known-limitations--failed-ideas).
- **Does not account for slippage around major news releases.** The backtest
  fills a stop exactly at the marked level (or at the next bar's open if price
  gapped straight through it, see `simulate_exit()`), which is the best a
  historical M1-bar simulation can do. In practice, a stop sitting just beyond
  a tight 1- or 5-minute NY-open range is exactly the kind of level that can
  slip significantly during high-impact news (NFP, CPI, FOMC, and NY-open
  itself is a common release window), real fills in fast conditions can be
  materially worse than the bar-close price this model assumes. This isn't
  modeled or corrected for anywhere in this strategy; treat the backtested
  numbers as an upper bound on what a live account would realize around news,
  not a guarantee.
- Spread, commission, and swap/financing are not modeled at all (the Daily FVG
  family at least models a flat commission; this strategy models neither).
- No look-ahead-bias audit trail like the Daily FVG family has, though the DST
  handling was explicitly verified (see the panel's own hint text and the
  numbers above).
- Wider targets (measured-move x2+, RR-mode) were only lightly explored, the
  x1/5-minute/bias-on combination is the best found so far, not a searched-for
  optimum across the full parameter space.

---

## Testing It

```python
from orb_backtest import run_orb_ny_open
trades = run_orb_ny_open("XAUUSD", bars=700000, candle_minutes=5, monitor_hours=6,
                          target_mode="measured_move", target_range_mult=1.0,
                          use_daily_bias=True)
print(trades.r_multiple.mean(), len(trades))
```

or via the API:

```python
import requests
r = requests.post("http://localhost:8001/api/orb-ny-open-backtest", json={
    "symbols": ["XAUUSD"], "candle_minutes": 5, "use_daily_bias": True,
})
print(r.json()["metrics"])
```
