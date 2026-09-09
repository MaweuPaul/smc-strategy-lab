# Legacy Strategies — HTF/LTF Sweep & ORB

Two strategies whose backend endpoints and code are still on disk, but which
are **not wired into any frontend tab** — they were dropped from the dashboard
before this documentation pass, for being comparatively weak. Kept here for
completeness and in case either is worth reviving with more work.

**Local research tool. Data is read-only from a running MetaTrader 5 terminal.
No orders are ever placed by any of this code.**

---

## ⚠️ Correction to an earlier claim

Mid-session, while deciding whether to remove P404 Sweep, ORB was tested with
`use_daily_bias=True` and reported as "flips profitable on 5/6 symbols — a
config fix, not a dead strategy." **That test used the wrong parameters** — it
left `range_minutes` and `target_mode` at their bare-function defaults (3
minutes, flat RR target) instead of the API's actual shipped config (30
minutes, measured-move target). Re-tested at the real shipped defaults, full
history (1.8M M1 bars, ~3.5 years):

| Symbol | n | Win% | avgR | PF |
|---|---|---|---|---|
| XAUUSD | 672 | 54.9% | +0.053 | 1.12 |
| USDJPY | 630 | 52.2% | +0.003 | 1.01 |
| EURUSD | 599 | 53.1% | +0.016 | 1.03 |
| GBPUSD | 598 | 51.5% | -0.012 | 0.98 |
| US30 | 662 | 50.0% | -0.036 | 0.93 |
| USTEC | 685 | 47.7% | -0.088 | 0.83 |

**ORB at its real defaults is flat-to-negative** — only Gold is marginally
positive, and PF 1.12 on ~672 trades is thin. The earlier "5/6 profitable"
claim should be discarded; it does not describe what the app actually ships.
This correction is recorded here so the mistake isn't repeated.

---

## HTF/LTF Sweep

The original sweep → MSS → OTE engine (Wyckoff-style: liquidity sweep, market
structure shift, optimal-trade-entry retracement) that the Asian Session and
Daily OB strategies both borrow pieces from (`find_confirmation()`,
`resolve_target()`, `simulate_exit()` all live in `htf_ltf_backtest.py`).

**Results** (H4 HTF, `min_rr=3.0`, `max_rr=5.0`, London/NY killzones — the
API's own defaults), `htf_bars=3000` ≈ last ~2 years on H4:

| Symbol | n | Win% | avgR | PF |
|---|---|---|---|---|
| US30 | 27 | 37.0% | +1.176 | 2.93 |
| USDJPY | 34 | 26.5% | +0.378 | 1.51 |
| USTEC | 47 | 25.5% | +0.270 | 1.36 |
| XAUUSD | 52 | 21.2% | +0.160 | 1.21 |
| EURUSD | 32 | 15.6% | -0.315 | 0.63 |
| GBPUSD | 36 | 11.1% | -0.447 | 0.50 |

This is the one legacy strategy with a genuinely strong single result (US30,
PF 2.93) — but on only 27 trades, which is a thin sample to trust. EURUSD and
GBPUSD lose clearly. Never re-tested with a longer `htf_bars` window or a
parameter sweep the way Daily FVG's levers were.

Files: `htf_ltf_backtest.py` (engine), `backend_api.py` (`POST /api/backtest`,
still live), `frontend/src/HtfLtfPanel.jsx` (not imported by `App.jsx`).

```bash
python htf_ltf_backtest.py --symbol US30 --htf H4 --htf-bars 3000 --min-rr 3.0 --max-rr 5.0
```

---

## ORB (Opening Range Breakout)

Marks the first N minutes of a session's trading (default: 30 minutes from
07:00 UTC) as the "opening range," then trades the breakout beyond it.

The concept is exactly what it sounds like — the market-open volatility burst,
where the volume that arrives at session open pushes price decisively one way
or the other. In this implementation, though, that's a proxy justification for
the *pattern*, not a literal input to it: the code triggers purely on **price**
closing beyond the opening range, using MT5's OHLC bars. MT5 forex/CFD feeds
only carry `tick_volume` (a count of price updates, not real traded volume),
and this strategy doesn't reference it at all — no volume threshold or filter
is part of the entry logic.

```
┌─────────────────────────────────────────────────────────────────┐
│                   ORB — DAILY TRADE LOGIC (M1)                   │
└─────────────────────────────────────────────────────────────────┘

  STEP 1: MARK THE OPENING RANGE
  ┌──────────────────────────────────────┐
  │  First `range_minutes` after session │
  │  open (default 30min @ 07:00 UTC)    │
  │  range_high / range_low from M1 bars │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 2: WATCH FOR THE BREAKOUT
  ┌──────────────────────────────────────┐
  │  First M1 CLOSE beyond either side   │
  │  of the range, within monitor_hours  │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 3: OPTIONAL DAILY BIAS FILTER
  ┌──────────────────────────────────────┐
  │  Only take the breakout if today's   │
  │  range midpoint > (or <) yesterday's │
  │  -- a cheap trend proxy, no HTF chart│
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 4: ENTER AT NEXT BAR'S OPEN
  ┌──────────────────────────────────────┐
  │  Stop: the OPPOSITE side of the range│
  │  Target: RR-based, OR a multiple of  │
  │  the range's own width               │
  │  ("measured_move" -- the shipped     │
  │  default, fits a noisy short range   │
  │  better than a flat RR)              │
  │  One trade per day (first valid      │
  │  breakout only)                      │
  └───────────────────────────────────────┘
```

Files: `orb_backtest.py` (engine), `backend_api.py` (`POST /api/orb-backtest`,
still live), `frontend/src/OrbPanel.jsx` (not imported by `App.jsx`).

```bash
# CLI defaults differ from the API's shipped config (3min range, flat RR) --
# pass the real config explicitly to reproduce the table above:
python orb_backtest.py --symbol XAUUSD --range-minutes 30 --target-mode measured_move --daily-bias
```
