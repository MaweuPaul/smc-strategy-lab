# strategytesting

A local research dashboard for backtesting Smart-Money-Concepts-style trading
strategies against real broker history, pulled live from a MetaTrader 5
terminal. FastAPI backend, React frontend, one tab per strategy.

**Local research tool. Data is read-only from a running MetaTrader 5 terminal.
No orders are ever placed by any code in this repository.**

---

## Running it

```bash
python -m uvicorn backend_api:app --reload --port 8001
```

```bash
cd frontend && npm run dev
```

A MetaTrader 5 terminal must be running and logged in on the same machine —
every backtest pulls data live from it. See any strategy doc's Setup section
for the full prerequisites.

---

## Strategies

### 🟢 Active (wired into the dashboard)

| Strategy | Doc | Status |
|---|---|---|
| **Daily FVG Retracement** | [DAILY_FVG.md](DAILY_FVG.md) | ⭐ Best-tested. Profitable on 7 of 8 symbols; H4/H1 structure bias filter measurably improves it. Extensively audited for look-ahead bias, with a documented history of what was tried and failed. |
| **Asian Session** | [ASIAN_SESSION.md](ASIAN_SESSION.md) | Profitable on most symbols at shipped defaults (Gold strongest); not yet swept the way Daily FVG has been. |
| **Daily Order Block** | [DAILY_OB.md](DAILY_OB.md) | Profitable on half of tested symbols at shipped defaults; same caveat as Asian Session. |
| **ORB (NY Open)** | [ORB_NY_OPEN.md](ORB_NY_OPEN.md) | Profitable on 4 of 6 symbols at shipped defaults (Gold strongest, PF 1.33). Distinct from the legacy session-open ORB below — anchored to the real, DST-aware 9:30am New York open. Does not model news-driven stop slippage. |

### ⚪ Legacy (code + endpoint on disk, no dashboard tab)

| Strategy | Doc | Status |
|---|---|---|
| **HTF/LTF Sweep** | [LEGACY_STRATEGIES.md](LEGACY_STRATEGIES.md#htfltf-sweep) | One strong result (US30) on a thin sample; weak on EURUSD/GBPUSD. Dropped from the UI before this doc pass. |
| **ORB** (Opening Range Breakout) | [LEGACY_STRATEGIES.md](LEGACY_STRATEGIES.md#orb-opening-range-breakout) | Flat-to-negative at its real shipped config across a 3.5-year test. An earlier session claim that it was "fixable with a daily bias filter" was based on testing the wrong parameters — see the correction at the top of that doc. |

### 🔴 Removed

| Strategy | Why |
|---|---|
| **P404 Sweep** | Lost money on all 6 tested symbols, on both of its internal engines, at multiple lookback windows. No configuration found that made it profitable anywhere. Fully removed — endpoint, frontend panel, and its MSS-only module (`p404_sweep_mss.py`) deleted. `p404_sweep_reversal.py` stays on disk only because it holds shared utility functions (`compute_atr14`, timezone offset) that the Daily FVG family imports — see [DAILY_FVG.md § System Components](DAILY_FVG.md#system-components). |

---

## Shared code

| File | Used by |
|---|---|
| `smc_backtest.py` | FVG detection, swing detection, slow daily structure bias — Daily FVG, Asian Session |
| `smc_structure.py` | Fast BOS/CHoCH structure (LuxAlgo definition) — Daily FVG's H4 bias filter |
| `p404_sweep_reversal.py` | `compute_atr14()`, MT5 server-timezone offset — Daily FVG family (not a strategy itself, see above) |
| `htf_ltf_backtest.py` | Sweep→MSS→OTE cascade, killzone/target/exit helpers — Asian Session, ORB, and its own (legacy) strategy |
| `discount_breakout_backtest.py` | Confirmed-swing detector — Daily FVG's resistance filter, Daily OB |
| `frontend/src/MonteCarlo.jsx` | Bootstrap resampler — Daily FVG, Daily OB |
| `frontend/src/Shared.jsx` | `Metric`, `EquityCurve`, `fmtMoney` — every panel |

---

## A note on how these were built

Every number in every strategy doc here came from actually running the code
and reading the output — not from reading the code and assuming what it does.
That distinction mattered at least twice: an ORB claim made mid-session turned
out to be wrong because it used the wrong default parameters (corrected in
[LEGACY_STRATEGIES.md](LEGACY_STRATEGIES.md)), and a promising-looking short
strategy for Daily FVG turned out to have its entire edge in a look-ahead bug
(documented in [DAILY_FVG.md](DAILY_FVG.md#known-limitations--failed-ideas)).
If you extend any of this, re-run it before trusting a number someone else
reported — including one in these docs.

---

## Contributing

Contributions and bug fixes are welcome. If you're adding a new filter or
strategy variant, hold it to the same bar the existing ones were built to —
see [DAILY_FVG.md § Testing Workflow](DAILY_FVG.md#testing-workflow) for the
four checks a result needs to pass before it's worth keeping (more than one
symbol, better than random, checked against a concrete failure case,
non-repainting). A pull request that adds a number without the script that
produced it is much harder to trust or review.

### Setup

You'll need a running, logged-in MetaTrader 5 terminal on the same machine —
see any strategy doc's Setup/Prerequisites section for details. The cTrader
integration (`ctrader_*.py`) is optional and separate from the main
dashboard; if you want it, copy `.env.example` to `.env` and follow the
instructions in that file to apply for API access at
[openapi.ctrader.com/apps](https://openapi.ctrader.com/apps) — approval is
manual and typically takes **2-3 business days**, so apply well before you
need it.
