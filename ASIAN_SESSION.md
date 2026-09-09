# Asian Session — Sweep/Reversal vs. Continuation

A two-sided (long and short) intraday strategy: trade the reversal if the
Asian session's liquidity gets swept, otherwise trade the session's own
breakout-and-retest.

**Local research tool. Data is read-only from a running MetaTrader 5 terminal.
No orders are ever placed by any of this code.**

> **Documentation depth note:** this strategy has not been put through the same
> depth of experimentation as [Daily FVG](DAILY_FVG.md) — no dedicated filter
> sweeps, no look-ahead-bias audit trail, no Monte Carlo. What follows documents
> the rule as coded and the baseline numbers measured at its actual shipped
> defaults. Treat it as a starting map, not a settled result.

---

## 📑 Table of Contents

- [What Is This?](#what-is-this)
- [Strategy Logic](#strategy-logic)
- [System Components](#system-components)
- [Settings Reference](#settings-reference)
- [Backtesting & Results](#backtesting--results)
- [Known Limitations](#known-limitations)
- [Testing It](#testing-it)

---

## What Is This?

Per UTC trading day, this strategy watches a fixed window (default 00:00–06:00
UTC, the Asian session) and picks one of two paths:

- **Reversal**: if a recent minor swing gets swept (wicked beyond, closed back
  inside) *during* that window, trade the reversal using the same
  sweep → MSS → OTE cascade as the HTF/LTF engine.
- **Continuation**: if no sweep happens, trade the session's own trend
  (bullish if it closed above its open, bearish otherwise) via a
  break-of-the-Asian-range-then-retest entry.

Both paths are still filtered to London/NY killzones — a valid setup outside
those windows is skipped.

---

## Strategy Logic

```
┌─────────────────────────────────────────────────────────────────┐
│              ASIAN SESSION — DAILY DECISION (per UTC day)        │
└─────────────────────────────────────────────────────────────────┘

  MARK THE ASIAN RANGE (default 00:00–06:00 UTC)
  ┌──────────────────────────────────────┐
  │  asian_high = session's high         │
  │  asian_low  = session's low          │
  │  asian_open / asian_close            │
  └──────────────┬───────────────────────┘
                 │
                 ▼
        Was a minor swing swept
        DURING the Asian window?
           │              │
          YES             NO
           │              │
           ▼              ▼
  ┌────────────────┐  ┌──────────────────────────────┐
  │   REVERSAL     │  │       CONTINUATION            │
  │ same sweep→MSS │  │ bias = session's own trend    │
  │ →OTE cascade   │  │ (close vs open)                │
  │ as HTF/LTF     │  │ wait for a CLOSE beyond the    │
  │ engine, scoped │  │ Asian range in that direction  │
  │ to the Asian   │  │ then wait for a RETEST of the  │
  │ window         │  │ range (break-and-retest)       │
  │ Stop: beyond   │  │ Stop: the OPPOSITE side of the │
  │ the sweep wick │  │ Asian range                    │
  └───────┬────────┘  └───────────────┬────────────────┘
          │                           │
          └─────────────┬─────────────┘
                         ▼
              MUST BE IN A KILLZONE
        (London 07-10 UTC or NY 12-15 UTC,
              configurable)
                         │
                         ▼
                  ENTER AT NEXT OPEN
              RR-based target (default 3-5R)
```

### Key concepts

| Term | Definition |
|---|---|
| **Sweep** | Price wicks beyond a minor swing high/low, then the candle closes back inside — a stop-hunt pattern |
| **MSS** | Market Structure Shift — the break confirming a reversal after the sweep |
| **OTE** | Optimal Trade Entry — the Fibonacci retracement zone used for the reversal entry |
| **Break-and-retest** | Continuation entry style: wait for a close outside the Asian range, then a pullback that retests it, before entering |
| **Killzone** | UTC hour window (London/NY open) that entries are restricted to, regardless of path taken |
| **HTF bias filter** | Optional: only take a trade whose direction agrees with H4/D1 structure (HH+HL/LH+LL) as of the day's Asian-session start |

---

## System Components

| File | Role |
|---|---|
| `asian_session_backtest.py` | `run_asian()` — the whole strategy: Asian range, sweep detection, reversal and continuation paths, HTF bias filter |
| `htf_ltf_backtest.py` | Shared: `find_confirmation()` (sweep→MSS→OTE), `in_killzone()`, `resolve_target()`, `simulate_exit()`, `summarize()` |
| `smc_backtest.py` | Shared: `find_swings()`, `structure_bias_series()` for the optional HTF bias filter |
| `backend_api.py` | `POST /api/asian-backtest` |
| `frontend/src/AsianPanel.jsx` | Settings sidebar, trade log, chart |

---

## Settings Reference

`POST /api/asian-backtest`

| Parameter | Default | Description |
|---|---|---|
| `symbols` | — | required, list |
| `ltf` | `M15` | Execution timeframe |
| `bars` | 20000 | LTF bars to fetch |
| `asian_start` / `asian_end` | 0 / 6 | UTC hours defining the Asian session |
| `min_rr` / `max_rr` | 3.0 / 5.0 | Target reward:risk range |
| `risk_pct` | 1.0 | % of equity risked per trade |
| `start_equity` | 10000 | |
| `mode_filter` | `all` | `all` \| `reversal` \| `continuation` — restrict to one path |
| `htf_bias_filter` | `None` (off) | `H4` or `D1` — require trade direction to agree with HTF structure |
| `htf_bias_bars` | 3000 | HTF bars fetched for the bias lookup |
| `allow_neutral_bias` | true | Whether a "neutral" HTF reading still lets trades through |

---

## Backtesting & Results

Measured at the API's own defaults (`min_rr=3.0`, `max_rr=5.0`, no HTF bias
filter, `bars=20000` M15 bars ≈ the last ~10 months):

| Symbol | n | Win% | avgR | PF |
|---|---|---|---|---|
| XAUUSD | 59 | 40.7% | +0.379 | 1.66 |
| US30 | 87 | 24.1% | +0.162 | 1.21 |
| USTEC | 76 | 23.7% | +0.066 | 1.09 |
| EURUSD | 69 | 29.0% | +0.042 | 1.06 |
| GBPUSD | 75 | 24.0% | -0.119 | 0.84 |
| USDJPY | 56 | 26.8% | -0.253 | 0.64 |

Gold is the strongest result. GBPUSD and USDJPY currently lose money at these
defaults. **This has not been swept across RR, killzone width, or the HTF bias
filter** the way Daily FVG's parameters have — these numbers describe the
as-shipped defaults, not a searched-for optimum.

---

## Known Limitations

- Same MT5 intraday-history caveat as Daily FVG — `bars=20000` M15 bars only
  reaches back to where the terminal's local cache is actually dense; see
  [DAILY_FVG.md §Known Limitations](DAILY_FVG.md#known-limitations--failed-ideas).
- No look-ahead-bias audit has been done on this file the way it was for the
  Daily FVG family. `find_confirmation()` and the HTF bias lookup are shared
  with `htf_ltf_backtest.py`, which has had less scrutiny this cycle.
- The win rates here (24–41%) are much lower than Daily FVG's (32–55%)
  because the RR target defaults far wider (3–5R) — that's a real design
  choice (fewer, bigger wins), not a red flag on its own, but it hasn't been
  compared against tighter RR the way Daily FVG's lever was.
- No Monte Carlo, no random-subsample check on the HTF bias filter, no
  seasonality view.

---

## Testing It

```bash
python asian_session_backtest.py --symbol XAUUSD
```

or via the API:

```python
import requests
r = requests.post("http://localhost:8001/api/asian-backtest", json={
    "symbols": ["XAUUSD"], "min_rr": 3.0, "max_rr": 5.0,
})
print(r.json()["metrics"])
```
