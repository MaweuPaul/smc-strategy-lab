# strategytesting

A local research dashboard for backtesting Smart-Money-Concepts-style trading
strategies against real broker history, pulled live from a MetaTrader 5
terminal. FastAPI backend, React frontend, one tab per strategy.

**Local research tool. Data is read-only from a running MetaTrader 5 terminal.
No orders are ever placed by any code in this repository.**

---

## 📑 Table of Contents

- [What Is This?](#what-is-this)
- [Why It's Built This Way](#why-its-built-this-way)
- [Running It](#running-it)
- [How Data Gets In — MT5, No API Key](#how-data-gets-in--mt5-no-api-key)
- [Strategies](#strategies)
  - [Daily FVG Retracement →](daily_fvg/README.md)
  - [Asian Session →](asian_session/README.md)
  - [Daily Order Block →](daily_ob/README.md)
  - [ORB — New York Open →](orb_ny_open/README.md)
- [Removed Strategies](#removed-strategies)
- [Credits & Inspiration](#credits--inspiration)
- [Repository Layout](#repository-layout)
- [Shared Code](#shared-code)
- [A Note on How These Were Built](#a-note-on-how-these-were-built)
- [Contributing](#contributing)
- [Roadmap / To Be Done](#roadmap--to-be-done)
- [Disclaimer](#disclaimer)

---

## What Is This?

A question that comes up constantly in discretionary Smart-Money-Concepts
trading (Fair Value Gaps, order blocks, liquidity sweeps, market structure
shifts) is: does this actually work, on this instrument, at these settings —
or does it just look convincing on the handful of charts someone screenshots?
This project exists to answer that question with code instead of eyeballing:
every rule here is written down precisely enough to run unattended across
years of real history, so "it works" becomes a number instead of a feeling.

It is deliberately **not** a signal bot or an execution system. There is no
order-placement code anywhere in this repository, on purpose — see
[Why It's Built This Way](#why-its-built-this-way). The output of this tool
is a verdict on a rule (profitable or not, on which symbols, under which
settings, with which known failure modes) and a chart replay of every trade
that rule took, so a human can decide whether to trade it by hand.

---

## Why It's Built This Way

A few design choices here aren't obvious, so it's worth writing down the
reasoning rather than leaving it to be reverse-engineered later:

**No execution path, anywhere, by design.** This is the single most
important property of the codebase. `MetaTrader5.initialize()` is used only
for its read-only history functions (`copy_rates_range`,
`copy_rates_from_pos`); nothing here ever calls an order-placement function.
That's not an oversight to be "improved" later — it means a bug in this
codebase can misreport a backtest, but it can never lose real money by
itself. Research code and execution code have very different risk profiles
and this project only wants the first one.

**One backend endpoint per strategy, not one generic backtest engine.**
Retracement timing, stop placement, sizing modes, and what counts as
"structure" genuinely differ between these four strategies. A single
mega-engine with enough flags to cover all of them would need heavy
branching, and every past bug that mattered here (look-ahead bias in
particular — see each strategy's Known Limitations) was found by reading a
short, specific function, not by tracing through a generic one. Duplication
between strategies is treated as an acceptable cost for auditability.

**A folder per strategy, but shared code stays shared.** Each active
strategy's own logic lives in its own folder so it can be read, tested, and
reasoned about in isolation. But a handful of things really are used by more
than one strategy — swing/structure detection, ATR, the sweep→MSS→OTE
cascade — and duplicating *those* would mean fixing the same bug in four
places. They stay as plain modules at the repo root (see
[Shared Code](#shared-code)) rather than being copied into every folder that
uses them.

**Flat imports plus a `sys.path` shim, not a proper installable package.**
Moving files into folders could have meant converting every
`from daily_fvg_newday import fetch` into `from daily_fvg.daily_fvg_newday
import fetch`, an `__init__.py` per folder, and a `pip install -e .` step.
For a single-developer local research tool where every script also needs to
run standalone for quick experiments (`python daily_fvg/daily_fvg_ltf_bias.py`
from anywhere), that ceremony wasn't worth it. Instead, `backend_api.py` adds
each strategy folder to `sys.path` at startup, and each strategy file adds
the repo root to its own `sys.path` — see [Repository
Layout](#repository-layout). Every existing import statement kept working
unchanged through the whole restructure.

**FastAPI + React, not a notebook.** A Jupyter notebook would be faster to
write a one-off backtest in, but this project runs the *same* rule
repeatedly across many parameter combinations and symbols while eyeballing
individual trades on a chart — a typed request model (Pydantic) catches a
bad parameter before a slow MT5 pull even starts, and a persistent React
panel makes flipping between "what does trade #47 actually look like" and
"what does the aggregate say" a click instead of a notebook re-run.

**Every claimed result is reproducible from a script in the repo.** See
[A Note on How These Were Built](#a-note-on-how-these-were-built) — this
isn't a style preference, it's caught real mistakes.

---

## Running It

```bash
python -m uvicorn backend_api:app --reload --port 8001
```

```bash
cd frontend && npm run dev
```

Open the frontend URL it prints and pick a tab. See [How Data Gets
In](#how-data-gets-in--mt5-no-api-key) below for the one real prerequisite.

---

## How Data Gets In — MT5, No API Key

Every strategy in this repo reads history through the `MetaTrader5` Python
package, which does **not** talk to a broker over the network and needs no
API key or secret of its own. Instead, `mt5.initialize()` connects to a
**MetaTrader 5 desktop terminal already running on this same machine** —
local interprocess communication, not an HTTP call. Whatever broker account
you're logged into inside that terminal app is the account this whole
dashboard reads from.

Concretely:

1. Install/open the MT5 terminal application and log into a broker account
   in its own UI, the normal way you'd use MT5 manually. That login is where
   the actual authentication happens — entirely outside this codebase.
2. Leave that terminal running.
3. Run the backend (`uvicorn backend_api:app`). Each backtest calls
   `mt5.initialize()`, which finds the running terminal and pulls
   `copy_rates_range` / `copy_rates_from_pos` bars from it.
4. If no terminal is running, `mt5.initialize()` can also auto-launch the
   last-installed terminal (from the path Windows registered at install
   time) — but it still needs that terminal's own saved/entered login to
   actually be authenticated; this code never supplies credentials for it.

That's why you won't find MT5 credentials anywhere in this repo or in
`.env` — there's nothing to configure. It's also why the exact symbols and
historical depth available depend entirely on your broker and what that
terminal's local history cache already holds (see each strategy's
"Known Limitations" section — this bit them more than once this project).

There is no other data source wired in. Nothing in this app calls out to a
network API for market data — MT5 is the only integration that exists.

---

## Strategies

### 🟢 Active (wired into the dashboard)

| Strategy | Status |
|---|---|
| **[Daily FVG Retracement](daily_fvg/README.md)** | ⭐ Best-tested. Profitable on 7 of 8 symbols; H4/H1 structure bias filter measurably improves it. Extensively audited for look-ahead bias, with a documented history of what was tried and failed. |
| **[Asian Session](asian_session/README.md)** | Profitable on most symbols at shipped defaults (Gold strongest); not yet swept the way Daily FVG has been. |
| **[Daily Order Block](daily_ob/README.md)** | Profitable on half of tested symbols at shipped defaults; same caveat as Asian Session. |
| **[ORB — New York Open](orb_ny_open/README.md)** | Profitable on 4 of 6 symbols at shipped defaults (Gold strongest, PF 1.33). Anchored to the real, DST-aware 9:30am New York open, not a fixed UTC hour. Does not model news-driven stop slippage. |

Each strategy lives in its own folder with its own `README.md` covering the
rule, the reasoning behind it, every setting, tested results, and known
failure modes.

### Removed Strategies

Three strategies were tried and fully removed — code, endpoint, and frontend
panel deleted, not just left dormant:

| Strategy | Why Removed |
|---|---|
| **P404 Sweep** | Lost money on all 6 tested symbols, on both of its internal engines, at multiple lookback windows. No configuration found that made it profitable anywhere. |
| **HTF/LTF Sweep** | One strong result (US30) on a thin sample (27 trades); weak on EURUSD/GBPUSD. Not worth the dashboard slot. |
| **ORB (session-open)** | The original version of ORB, marking a range for N minutes *after* a fixed UTC session open. Flat-to-negative at its real shipped config across a 3.5-year test — an earlier claim that it was "fixable with a daily bias filter" turned out to be based on testing the wrong parameters. Replaced by the NY-open ORB above, which is a genuinely different rule. |

`p404_sweep_reversal.py` and `htf_ltf_backtest.py` remain on disk — not as
strategies, but because active strategies import shared utility functions
from them (see [Shared Code](#shared-code)).

---

## Credits & Inspiration

The removed **P404 Sweep** strategy (and its name) was this project's own
mechanical translation of the **PHASE 404** system by
[@PHASE4o4](https://www.instagram.com/phase4o4/) — a published, fully
mechanical Smart Money Concepts trading system (TradingView indicator + MT5
Expert Advisor + backtesting config) built around a specific sequential
cascade: mark liquidity (BSL/SSL) → wait for a sweep (the Wyckoff
Accumulation-Manipulation-Distribution "manipulation" phase) → confirm a
Market Structure Shift → wait for price to retrace into the Optimal Trade
Entry zone (a 61.8–78.6% Fibonacci pullback) → optionally require SMT
(Smart Money Technique) divergence against a correlated pair → enter, gated
to the London/NY kill zones only.

Worth being precise about what this project's testing does and doesn't say
about that system: this repo's P404 implementation lost money on every
tested symbol and was removed (see [Removed Strategies](#removed-strategies)
above), but that's a statement about *this specific mechanical translation*,
on *these symbols*, over *this data window* — not a verdict on the PHASE 404
system as published, which has its own settings, optimization guidance, and
walk-forward testing process that this project's version didn't fully
reproduce. `smc_structure.py`'s BOS/CHoCH detection (used by Daily FVG's
bias filter) is separately ported from the LuxAlgo "Smart Money Concepts"
Pine Script indicator, a different (if related) source.

If you're interested in the original methodology and its own tooling, PHASE
404's creator posts about it at
[instagram.com/phase4o4](https://www.instagram.com/phase4o4/).

---

## Repository Layout

```
strategytesting/
├── backend_api.py          # FastAPI app — one endpoint per active strategy
├── daily_fvg/               # Daily FVG Retracement — engine + README.md
├── asian_session/            # Asian Session — engine + README.md
├── daily_ob/                 # Daily Order Block — engine + README.md
├── orb_ny_open/               # ORB (New York Open) — engine + README.md
├── smc_backtest.py          # shared: FVG/swing detection, daily structure bias
├── smc_structure.py         # shared: fast BOS/CHoCH structure (LuxAlgo definition)
├── discount_breakout_backtest.py  # shared: confirmed-swing detector
├── htf_ltf_backtest.py      # shared: sweep→MSS→OTE helpers (not a strategy itself)
├── p404_sweep_reversal.py   # shared: compute_atr14, MT5 tz offset (not a strategy itself)
└── frontend/                # React app, one panel per active strategy
```

Each strategy folder is importable as flat modules (not a Python package) —
see [Why It's Built This Way](#why-its-built-this-way) for the reasoning.
`backend_api.py` adds every strategy folder to `sys.path` at startup, and
each strategy file adds the repo root to its own `sys.path` too, so a script
works identically whether it's imported by the backend or run standalone
from anywhere, e.g. `python daily_fvg/daily_fvg_ltf_bias.py`.

---

## Shared Code

| File | Used by |
|---|---|
| `smc_backtest.py` | FVG detection, swing detection, slow daily structure bias — Daily FVG, Asian Session |
| `smc_structure.py` | Fast BOS/CHoCH structure (LuxAlgo definition) — Daily FVG's H4 bias filter |
| `p404_sweep_reversal.py` | `compute_atr14()`, MT5 server-timezone offset — Daily FVG family (not a strategy itself) |
| `htf_ltf_backtest.py` | Sweep→MSS→OTE cascade, killzone/target/exit helpers — Asian Session, Daily OB, ORB (not a strategy itself — its own strategy was removed) |
| `discount_breakout_backtest.py` | Confirmed-swing detector — Daily FVG's resistance filter, Daily OB |
| `frontend/src/MonteCarlo.jsx` | Bootstrap resampler — Daily FVG, Daily OB |
| `frontend/src/Shared.jsx` | `Metric`, `EquityCurve`, `fmtMoney` — every panel |

---

## A Note on How These Were Built

Every number in every strategy doc here came from actually running the code
and reading the output — not from reading the code and assuming what it does.
That distinction mattered at least twice: an ORB claim made mid-session turned
out to be wrong because it used the wrong default parameters (see the
"Removed strategies" table above), and a promising-looking short strategy for
Daily FVG turned out to have its entire edge in a look-ahead bug (documented
in [daily_fvg/README.md](daily_fvg/README.md#known-limitations--failed-ideas)).
If you extend any of this, re-run it before trusting a number someone else
reported — including one in these docs.

---

## Contributing

Contributions and bug fixes are welcome. If you're adding a new filter or
strategy variant, hold it to the same bar the existing ones were built to —
see [daily_fvg/README.md § Testing Workflow](daily_fvg/README.md#testing-workflow)
for the four checks a result needs to pass before it's worth keeping (more
than one symbol, better than random, checked against a concrete failure
case, non-repainting). A pull request that adds a number without the script
that produced it is much harder to trust or review.

Setup is just [MT5, no API key](#how-data-gets-in--mt5-no-api-key) — nothing
else to configure.

---

## Roadmap / To Be Done

- **TradingView Pine Script indicators for the validated strategies.** This
  project currently only backtests — trading any of these by hand means
  reading a backtested rule off this dashboard and re-marking the same
  levels manually on a live chart. A Pine Script v5 indicator per strategy
  (Daily FVG's gap + H4/H1 bias, the Asian range + sweep markers, order
  blocks, the NY-open marking range) would draw exactly what the backtest
  engine detects directly on a TradingView chart, closing the gap between
  "this rule tested well" and "here's where it fires live" without
  re-deriving the levels by eye each time. Not started yet.

---

## Disclaimer

This is a private research tool, not a published or distributed trading
product, and nothing in this repository is financial advice. It never
places, modifies, or cancels a live order. Every result in every doc here
comes from historical simulation against data already cached in a local
MetaTrader 5 terminal.

A profitable backtest is not a promise. Every number in this repository is
in-sample, measured on one broker's historical data, and past performance,
backtested or otherwise, is not indicative of future results. Markets
change, brokers differ in spread/slippage/execution, and a rule that held up
here can stop working with no warning. Treat every strategy in this
repository as a probabilistic edge, not a certainty: even the best-tested
result above still loses on individual trades, sometimes many in a row (see
each strategy's Known Limitations for real examples), and win rate alone was
shown more than once in this project's own testing to be a misleading
measure of whether a rule is actually profitable.

If you ever trade any of this on a real account, do so at your own risk, with
money you are fully prepared to lose, and never with capital you need for
anything else. Use proper risk and position management on every single
trade, and never risk more than you can afford to lose in total. Nothing
here should be mistaken for a guarantee, a signal service, or advice tailored
to your own financial situation.
