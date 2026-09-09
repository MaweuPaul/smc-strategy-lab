# Daily FVG Retracement

A mechanical, long-only Fair Value Gap strategy — daily bias, M15 execution,
lower-timeframe structure confirmation — with a Python backtest engine, a
FastAPI backend, and a React panel for inspecting every trade it ever took.

**Local research tool. Data is read-only from a running MetaTrader 5 terminal.
No orders are ever placed by any of this code.**

---

> See the [main README](../README.md#-strategies) for the table of contents
> across all strategies.

## What Is This?

This is a fully mechanical trading strategy: wait for price to leave an
imbalance on the daily chart, wait for it to come back, buy the retracement,
confirmed by fast lower-timeframe structure. It removes discretion from a
specific, narrow setup and enforces the same rule on every symbol, every time.

The system has three parts:

| Component | File(s) | Purpose |
|---|---|---|
| **Backtest engine** | `daily_fvg_newday.py`, `smc_backtest.py`, `smc_structure.py` | Detect FVGs, simulate trades bar-by-bar, compute structure bias |
| **API** | `backend_api.py` | `POST /api/daily-fvg-backtest` — one endpoint, every parameter below |
| **Panel** | `frontend/src/DailyFvgPanel.jsx` + `DailyFvgReplay.jsx` + `MonteCarlo.jsx` | Settings sidebar, trade log, seasonality, Monte Carlo, chart replay |

This is **not** a signal bot and there is no execution path to a live account
anywhere in this codebase. It is a research tool for deciding whether a rule
has an edge before anyone trades it by hand.

---

## Why This Might Work

The underlying idea, as it's usually described in Smart-Money-Concepts
material: a Fair Value Gap is a 3-candle imbalance — one candle displaced so
hard that a band of price never saw two-way trading. Because so few orders
were actually matched there, the theory goes, that band still holds unfilled
interest, and price tends to be "drawn back" to it before continuing. Buying
that revisit is a bet that the original move's demand is still intact.

This project doesn't take that theory on faith — it's the reason for
everything in [Known Limitations & Failed Ideas](#known-limitations--failed-ideas)
below. Specifically, testing it against itself is what falsified the
strongest version of the claim:

- **If "any imbalance is a magnet" were true symmetrically**, shorting
  retracements into *bearish* gaps should work about as well as buying
  bullish ones. It doesn't — it loses money on every tested symbol (see
  [Bearish mirror](#bearish-mirror--no-edge-in-either-direction)). The gap
  itself isn't what's carrying the edge.
- **What actually moves the numbers is trend context.** Filtering bullish
  retracements by fast H4/H1 structure (only buy when the faster timeframe
  agrees) measurably improves win rate, profit factor, *and* drawdown (see
  [§ With the H4/H1 bias filter](#with-the-h4h1-bos-choch-bias-filter-)).
  That's consistent with a narrower, more mundane explanation than "gaps are
  magnets": these particular instruments (Gold, the indices) carry a real
  upward drift most of the time, and a bullish FVG retracement is a
  reasonably precise, mechanical way to time an entry into a dip *within*
  that drift — the edge is closer to "buy pullbacks in an uptrend, using the
  gap as the timing tool" than "unfilled imbalances always get revisited."

So treat the name literally: this strategy is not evidence that Fair Value
Gaps are magnets in general. It's evidence that one specific, narrow
application of the idea — bullish gaps, retracement entries, on trending
instruments, filtered by faster structure — has held up under testing on
this data. That distinction is the whole reason the failed variants below
are documented as thoroughly as the working one.

---

## Strategy Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   DAILY FVG RETRACEMENT — TRADE LOGIC            │
└─────────────────────────────────────────────────────────────────┘

  STEP 1: DETECT THE GAP (D1)
  ┌──────────────────────────────────────┐
  │  3-candle imbalance:                 │
  │  low[i+1] > high[i-1] = bullish FVG  │
  │  Known only once candle i+1 CLOSES   │
  │  formed_at = i+1                     │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 2: WAIT FOR RETRACEMENT
  ┌──────────────────────────────────────┐
  │  Price trades back into the gap:     │
  │  day's range overlaps [bottom, top]  │
  │  (or: skip this, fire on the very    │
  │  next D1 open = "continuation" mode) │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 3: CONFIRM H4/H1 BIAS (optional, on by default)
  ┌──────────────────────────────────────┐
  │  BOS/CHoCH structure (LuxAlgo def):  │
  │  bias flips when close crosses the   │
  │  last unbroken pivot                 │
  │  Require BULLISH bias at entry time  │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 4: ENTRY (M15)
  ┌──────────────────────────────────────┐
  │  Buy at the OPEN of the next D1      │
  │  bar's first M15 candle              │
  │  One trade at a time, one shot per   │
  │  gap                                 │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 5: STOP LOSS
  ┌──────────────────────────────────────┐
  │  Gap's bottom edge                   │
  │  minus ATR_BUFFER_MULT × D1 ATR14    │
  │  (ATR measured as of gap formation)  │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 6: TARGET
  ┌──────────────────────────────────────┐
  │  Fixed reward:risk multiple of the   │
  │  stop distance. Single exit.         │
  │  No partials, no trailing.           │
  └──────────────┬───────────────────────┘
                 │
                 ▼
  STEP 7: EXIT PRIORITY (M15, bar by bar)
  ┌──────────────────────────────────────┐
  │  1. Gap through stop (bar opens      │
  │     beyond it) → fill at that open   │
  │  2. Stop touched intrabar → exit     │
  │  3. Target touched intrabar → exit   │
  │  Stop checked BEFORE target on an    │
  │  ambiguous bar (pessimistic)         │
  └───────────────────────────────────────┘
```

### Key Concepts Defined

| Term | Definition |
|---|---|
| **FVG** | Fair Value Gap — a 3-candle imbalance; the middle candle left a price band nobody traded through |
| **Retracement** | Waiting for price to trade back into a formed FVG before entering |
| **Continuation** | Entering right after the FVG confirms, no retracement wait |
| **`formed_at`** | The D1 bar index at which a gap becomes knowable (one bar after the imbalance) |
| **ATR buffer** | Stop distance beyond the gap edge, in multiples of D1 ATR14 as of gap formation |
| **RR / reward:risk** | Target distance as a multiple of the stop distance |
| **BOS** | Break of Structure — close crosses the last unbroken pivot *in the direction of* the existing trend |
| **CHoCH** | Change of Character — the same break, but *against* the existing trend (a reversal signal) |
| **Structure bias** | Bullish/bearish/neutral state that flips on a BOS or CHoCH; read on H4 or H1 for this strategy |
| **Non-repainting** | A rule that only ever uses data knowable at the moment it fires — audited explicitly, see below |
| **avgR** | Average result per trade, in multiples of R (risk) |
| **PF (profit factor)** | Gross profit ÷ gross loss. Above 1 = profitable |
| **Max DD (drawdown)** | Largest peak-to-trough equity decline over the run |
| **Monte Carlo** | Bootstrap resample of the same trades into random orderings, to separate skill from sequence luck |

---

## System Components

### Backtest engine

| File | Role |
|---|---|
| `daily_fvg_newday.py` | **Core.** `fetch()` (MT5 data pull), `find_entries()` (retracement), `simulate()`, `summarize()`. Tunables are module-level constants, monkey-patched per request. |
| `smc_backtest.py` | `find_fvgs()` — the 3-candle FVG detector every variant shares. Also `find_swings()`, `structure_bias_series()` (the *slow* daily structure read — see Failed Ideas). |
| `smc_structure.py` | **BOS/CHoCH structure**, ported from the LuxAlgo Smart Money Concepts Pine script. Bias flips the moment close crosses the last unbroken pivot. |
| `p404_sweep_reversal.py` | Shared utilities only: `compute_atr14()` (Wilder), MT5 server-timezone offset helper. Not a strategy itself — see [Repository Structure](#repository-structure). |

### Variants and experiments

| File | Verdict |
|---|---|
| `daily_fvg_continuation.py` | ✅ Works on some symbols. Buys the open right after the gap confirms, no retracement wait. |
| `daily_fvg_ltf_bias.py` | ⭐ **Best result.** H4/H1 BOS-CHoCH bias filter. |
| `daily_fvg_resistance_filter.py` | 🟡 Marginal. Net positive only on GBPUSD. |
| `daily_fvg_winrate_levers.py` | RR sweep × 200D SMA trend filter. Established Gold's 2:1 default. |
| `daily_fvg_extension_filter.py` | ❌ Failed. Over-extension doesn't predict outcome — see Failed Ideas. |
| `daily_fvg_structure_filter.py` | ❌ Failed. *Daily* structure bias — flips too late. |
| `daily_fvg_ride_to_magnet.py` | ❌ Failed — look-ahead bias. |
| `daily_fvg_bearish.py`, `daily_fvg_bearish_ltf_bias.py` | ❌ Failed. Bearish mirror, both unfiltered and H4-bias-filtered. |
| `daily_fvg_combined.py` | Merges retracement + continuation entries into one stream. |

### Application

| File | Role |
|---|---|
| `backend_api.py` | `POST /api/daily-fvg-backtest` — the single endpoint the panel calls. |
| `frontend/src/DailyFvgPanel.jsx` | The panel: settings sidebar, metrics, trade log, seasonality, Monte Carlo, chart. |
| `frontend/src/DailyFvgReplay.jsx` | lightweight-charts v5 replay — FVG zone, entry/stop/target lines, markers, hover OHLC. |
| `frontend/src/MonteCarlo.jsx` | Bootstrap resampler (shared with the Daily OB panel). |

---

## Installation & Setup

### Prerequisites

- **A MetaTrader 5 terminal must be running and logged into a broker account on
  the same machine.** The Python `MetaTrader5` package talks to that local
  terminal process directly — it is not a network API, and there is no way to
  fetch data without a terminal open.
- Every symbol you intend to test must be visible in the terminal's **Market
  Watch**. `fetch()` calls `mt5.symbol_select(symbol, True)`, which adds a
  symbol automatically if the terminal already knows it from your broker — but
  the broker has to offer it first.
- See [Known Limitations](#known-limitations--failed-ideas): M15/H1/H4 history
  on this terminal only goes back to ~2021-07. D1 goes back much further.

### Running the app

Servers are defined in `.claude/launch.json`:

```bash
python -m uvicorn backend_api:app --reload --port 8001
```

```bash
cd frontend && npm run dev
```

Open the frontend and pick the **Daily FVG** tab.

### How data is pulled

There is no separate "download" step — every backtest run pulls fresh data at
request time, straight from the terminal:

```python
def fetch(symbol, timeframe, start, end):
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    if not mt5.symbol_select(symbol, True):
        mt5.shutdown()
        raise RuntimeError(f"Could not select symbol {symbol}")
    offset_h = _server_utc_offset_hours(symbol)
    rates = mt5.copy_rates_range(symbol, timeframe, start, end)
    mt5.shutdown()
    ...
```

(`daily_fvg_newday.py`, `fetch()`.) Three things worth knowing:

- It calls `mt5.shutdown()` when done — every `fetch()` call is a fresh
  connect → select → read → disconnect cycle, not a persistent session. Reading
  `symbol_info()` *after* a `fetch()` call sees a disconnected terminal and
  silently returns defaults instead of erroring — a real bug this caused once,
  now fixed with a dedicated `get_symbol_info()` that opens its own connection.
- `copy_rates_range()` only returns bars the terminal has **already downloaded
  and cached locally**. It does not reach out to the broker for missing
  history, and does not error when history is thin.
- `_server_utc_offset_hours()` corrects for the broker's server timezone, so
  `time` columns come back as true UTC.

To pull data yourself, e.g. in a Python REPL:

```python
import MetaTrader5 as mt5
from datetime import datetime
from daily_fvg_newday import fetch

d1 = fetch("XAUUSD", mt5.TIMEFRAME_D1, datetime(2020, 1, 1), datetime.now())
print(d1.tail())
```

---

## Settings Reference

`POST /api/daily-fvg-backtest`

#### 💰 Core

| Parameter | Default | Description |
|---|---|---|
| `symbol` | — | required. One of USTEC, US500, US30, EURUSD, XAUUSD, XAGUSD, GBPUSD, USDJPY |
| `months_back` | 96 | History depth, 1–300 |
| `start_equity` | 100000 | Starting account balance |

#### 📊 Entry

| Parameter | Default | Description |
|---|---|---|
| `entry_style` | `retracement` | or `continuation` — see Strategy Architecture, Step 2 |
| `target_rr` | 2.0 | The main win-rate lever — closer target wins more, wins less each time |
| `atr_buffer_mult` | 0.25 (1.0 metals, 0.75 USDJPY) | Stop distance beyond the gap, in ATR14 multiples. Wider is better almost everywhere |

#### 🎯 H4/H1 Bias Filter

| Parameter | Default | Description |
|---|---|---|
| `use_ltf_bias` | **true** (false for US500) | Require confirmed structure bias at entry — see Backtesting & Results |
| `ltf_bias_timeframe` | `H4` | or `H1` |
| `ltf_bias_size` | 5 | Pivot lookback bars. 5 = LuxAlgo "internal structure" default, 50 = LuxAlgo "swing structure" default |

#### 🛡️ Other Filters

| Parameter | Default | Description |
|---|---|---|
| `use_resistance_filter` | false (true for GBPUSD) | Skip a buy if an unbroken D1 swing high sits closer than the target. Retracement-only |
| `use_magnet_leg` | **false everywhere** | Short leg into unfilled gaps. Tested and failed (look-ahead bias) — kept as an inspectable toggle only |
| `magnet_leg_risk_mult` | 0.5 | Irrelevant while `use_magnet_leg` is off |

#### ⚙️ Position Sizing

| Parameter | Default | Description |
|---|---|---|
| `sizing_mode` | `risk_pct` | or `fixed_units` |
| `risk_pct` | 1.5 | % of current equity risked per trade (percent-risk mode) |
| `fixed_lots` | 0.10 | Real MT5 lots, converted via the symbol's contract size (fixed-size mode) |

The module-level constants in `daily_fvg_newday.py` are overwritten per
request. Fine for a single-user local tool; **not thread-safe** for concurrent
use.

---

## Backtesting & Results

### Baseline (long-only, per-symbol tuned RR/ATR buffer, no filters, 1.5% risk)

| Symbol | n | Win% | avgR | PF | Max DD | Final ($100k start) |
|---|---|---|---|---|---|---|
| XAUUSD | 103 | 55.3% | +0.615 | 2.37 | -10.5% | $241,608 |
| XAGUSD | 67 | 34.3% | +0.340 | 1.52 | -10.3% | $134,999 |
| USDJPY | 116 | 42.2% | +0.199 | 1.28 | -15.1% | $121,763 |
| USTEC | 191 | 38.2% | +0.103 | 1.13 | -19.3% | $112,955 |
| US30 | 193 | 37.8% | +0.075 | 1.09 | -27.2% | $98,856 |
| US500 | 186 | 36.0% | +0.011 | 1.03 | -23.1% | $88,565 |
| GBPUSD | 171 | 38.6% | +0.045 | 1.04 | -36.7% | $83,931 |
| EURUSD | 162 | 31.5% | -0.176 | 0.73 | -49.6% | $50,413 |

Gold is the flagship. EURUSD has no edge; GBPUSD is marginal.

### With the H4/H1 BOS-CHoCH bias filter ⭐

Only take the trade when lower-timeframe structure reads bullish at entry.
Improves win rate, avg R, profit factor, drawdown **and** worst losing streak
on 5 of 6 symbols tested:

| Symbol | Baseline (Win% / avgR / PF / DD) | With H4 bias |
|---|---|---|
| XAUUSD | 55.3% / +0.615 / 2.37 / -10.5% | **58.3% / +0.689 / 2.52 / -7.4%** |
| XAGUSD | 34.3% / +0.340 / 1.52 / -10.3% | **43.8% / +0.653 / 2.21 / -7.4%** |
| US30 | 37.8% / +0.075 / 1.09 / -27.2% | **41.2% / +0.175 / 1.25 / -11.3%** |
| USDJPY | 42.2% / +0.199 / 1.28 / -15.1% | **42.9% / +0.229 / 1.34 / -8.7%** |
| USTEC | 38.2% / +0.103 / 1.13 / -19.3% | 37.8% / +0.098 / 1.14 / **-13.1%** |
| US500 | 36.0% / +0.011 / 1.03 / -23.1% | 34.2% / -0.056 / 0.89 / -25.4% ✗ |

US30's worst losing run drops from **12 to 6**. US500 is the one exception
(defaults **off** there).

**This is real trade selection, not just trading less.** Against 2,000 random
subsamples of the same size, the filter's drawdown reduction beat 90.8% (Gold),
92.5% (USTEC), 99.8% (US30), 90.1% (USDJPY) and 87.5% (Silver) of random picks.
US500 scored 19.2% — worse than random, hence the default.

It roughly halves trade count, so absolute return falls at fixed risk — but
drawdown falls further, so raising risk converts the saved drawdown into
return:

| Symbol | Baseline @1.5% risk | Filtered @3% risk |
|---|---|---|
| XAUUSD | $194,086 / -10.5% | **$234,691** / -12.0% |
| XAGUSD | $131,632 / -10.3% | **$209,252** / -11.8% |
| USTEC | $102,375 / -19.3% | **$136,998 / -16.3%** |
| US30 | $111,124 / -23.3% | **$153,188 / -21.4%** |

### Judging a result

There's no universal "good" threshold here — Gold's PF 2.37 is not comparable
to EURUSD's structurally weaker edge. What matters is the direction of change
against the *same symbol's own baseline*: PF and final equity moving together,
without drawdown moving the wrong way at the same risk level. A win-rate
increase on its own means nothing — see the RR lever below.

| Symbol | avgR | Win% | PF | Notes |
|---|---|---|---|---|
| XAUUSD @ 2:1 | +0.614 | 55.3% | 2.37 | Better than 3:1 on every measure that matters |
| XAUUSD @ 1:1 | — | 62.7% | 1.57 | Higher win rate, but final equity is *lower* ($167,694 vs $241,172) |
| USTEC @ 1:1 | — | 54.7% | 1.08 | Higher win rate, but this configuration **loses money** |

A win rate above 50% is trivially available on any symbol by shortening the
target — four of six symbols lose money doing it. Judge by profit factor and
final equity, never win rate alone.

---

## Known Limitations & Failed Ideas

### The M15/H1/H4 data gap (important)

The MT5 Python API returns only bars the **terminal has already cached
locally** — it does not fetch missing history from the broker, and does not
error when history is absent. Instead it silently returns **one degenerate bar
per day** whose OHLC is just that day's full range.

On this terminal, real intraday data begins around **2021-07-16**. Before
that, every M15/H1/H4 request returns fake daily-resolution bars.

| Consequence | Detail |
|---|---|
| Entries | Unaffected — entry is the day's open, which the fake bar preserves exactly |
| Exit ordering pre-2021-07 | Unreliable — a single full-day bar can't say whether stop or target was touched first. The engine resolves stop-first (pessimistic, not necessarily accurate) |
| H4 bias filter | Refuses to run on that region — detects where the feed becomes genuinely intraday (`reliable_intraday_start()` in `backend_api.py`) and truncates the backtest rather than computing structure from fake bars |

To extend it: open each symbol's M15/H4 chart in the MT5 terminal and scroll
back (Home/PgUp) to force a history download. The Python API cannot trigger
this itself.

### Ride to the magnet — look-ahead bias

Shorting into an unfilled gap, targeting the fill. Originally showed **+0.65R
on Gold, +0.64R on USTEC**. The entry triggered off a bar's **low** touching
the gap, then filled at that same bar's **open** — but the low isn't known
until the bar closes, and the open sits above the trigger level ~99% of the
time (mean **$10.9** on Gold — roughly half an R of free profit no live fill
could ever get). Filling honestly at the trigger level flips every symbol
negative (Gold -0.24R, USTEC -0.62R).

### Daily structure filter — flips too late

HH/HL vs LH/LL bias on the D1 chart. Structure does carry real signal, but
filtering on it loses money: in the Jan–Feb 2026 Gold selloff, 3 of 5
consecutive losses fired while daily structure still read bullish. Moving to
H4/H1 (which flips on the first close through a pivot, not on two confirmed
swings) fixed this — hence the current filter.

### Over-extension filter — the entry mechanically unwinds it

Tested skipping a buy when price sits far above its own mean
(`(close - SMA50) / ATR14`). Fails because **losers are less extended than
winners**, not more (Gold: +1.44 vs +1.70) — the retracement entry itself
requires price to fall first, which unwinds most of the extension before entry
ever fires. Gold's drawdown was identical at every threshold tested.

### 200D SMA trend filter — too slow

Failed on 5 of 6 symbols. A slow moving average sits below price for weeks
into a selloff, so it never blocks the trades that hurt.

### Bearish mirror — no edge in either direction

A mirrored short strategy (sell retracements into bearish FVGs) loses money on
every symbol, both unfiltered and with the same H4 bias filter (requiring
bearish structure) that fixed the long side. These instruments carry a
persistent long-side drift; shorting them has no edge here regardless of
confirmation speed.

### On loss clusters

Runs of consecutive losses look like a trend effect but are mostly chance.
Shuffling Gold's own 103 trade outcomes into random order 20,000 times gives a
mean longest losing streak of 5.2, and a run of 7+ occurs 15.2% of the time.

### What this backtest does not model

- Spread, slippage, swap/financing, and any commission beyond the modeled
  0.05% per fill.
- Real order queueing — target/stop fills assume the exact level fills.
- Any out-of-sample period. Every number above is in-sample on one broker's
  history, and per-symbol defaults were chosen by sweeping this same data —
  some of their edge is selection, not signal.

---

## Testing Workflow

Every feature and every failure above was found the same way: write a small
standalone script, run it directly, read the numbers, decide. Nothing gets
wired into the panel until it clears this bar.

```bash
python daily_fvg_ltf_bias.py          # or any other daily_fvg_*.py file
```

Each script follows the same shape: fetch data, build entries with
`find_entries()` or a variant, run `simulate()`, print `summarize()`'s
win/avgR/PF/drawdown table. `daily_fvg_winrate_levers.py` and
`daily_fvg_extension_filter.py` both do a clean two-step — "does X predict
outcome, then does filtering on X help" — worth copying for a new idea rather
than starting from scratch.

**A result is not worth keeping until it passes:**

1. **More than one symbol.** A single-symbol win is very likely noise.
2. **Better than random.** For any filter, check it beats randomly dropping
   the same number of trades (see the H4 bias filter's random-subsample test
   above). A filter that "helps" only because it trades less isn't a filter.
3. **A concrete failure case.** If you have a specific losing trade in mind,
   print exactly what the rule would have said *at that moment* — not just
   aggregate stats, which can hide a rule that never would have blocked the
   loss you were trying to fix.
4. **Non-repainting.** Confirm the logic only reads data actually knowable at
   decision time. This caught two real bugs: the magnet leg's look-ahead fill,
   and computing "H4 structure" on degenerate pre-2021 daily-resolution bars.

### Testing via the API directly

```bash
curl -s -X POST http://localhost:8001/api/daily-fvg-backtest \
  -H "Content-Type: application/json" \
  -d '{"symbol":"XAUUSD","months_back":96,"target_rr":2.0,"atr_buffer_mult":1.0,
       "use_ltf_bias":true,"ltf_bias_timeframe":"H4","ltf_bias_size":5}' \
  | python -m json.tool
```

```python
import requests

for symbol in ["XAUUSD", "USTEC", "US30"]:
    r = requests.post("http://localhost:8001/api/daily-fvg-backtest", json={
        "symbol": symbol, "months_back": 96, "target_rr": 2.0,
        "atr_buffer_mult": 1.0, "use_ltf_bias": True,
    })
    m = r.json()["metrics"]
    print(symbol, m["trades"], m["win_rate"], m["avg_r"], m["profit_factor"])
```

---

## Repository Structure

```
strategytesting/
│
├── daily_fvg_newday.py              # Core engine: fetch, find_entries, simulate
├── smc_backtest.py                  # FVG detector + slow daily structure bias
├── smc_structure.py                 # LuxAlgo-faithful BOS/CHoCH structure
├── p404_sweep_reversal.py           # Shared utils (compute_atr14, tz offset) —
│                                     #   NOT the P404 strategy, which was removed
│
├── daily_fvg_continuation.py        # Entry style variant (works on some symbols)
├── daily_fvg_ltf_bias.py            # ⭐ H4/H1 bias filter (best result)
├── daily_fvg_resistance_filter.py   # Marginal filter (GBPUSD only)
├── daily_fvg_winrate_levers.py      # RR sweep, established Gold's 2:1 default
├── daily_fvg_extension_filter.py    # ❌ failed
├── daily_fvg_structure_filter.py    # ❌ failed (daily structure, too slow)
├── daily_fvg_ride_to_magnet.py      # ❌ failed (look-ahead bias)
├── daily_fvg_bearish.py             # ❌ failed (bearish mirror)
├── daily_fvg_bearish_ltf_bias.py    # ❌ failed (bearish + H4 bias)
├── daily_fvg_combined.py            # Merges retracement + continuation
│
├── backend_api.py                   # FastAPI app, every endpoint
│
└── frontend/
    └── src/
        ├── DailyFvgPanel.jsx        # Settings, metrics, trade log, seasonality
        ├── DailyFvgReplay.jsx       # Chart replay
        ├── MonteCarlo.jsx           # Bootstrap resampler (shared component)
        ├── AsianPanel.jsx           # Other kept strategy
        ├── DailyObPanel.jsx         # Other kept strategy
        └── App.jsx                 # Tab wiring
```

---

## FAQ

**Q: Why is this long-only? Isn't that leaving trades on the table?**
A: Tested, not assumed. A bearish mirror loses money on every symbol, and
re-testing it with the same H4 bias filter that fixed the long side still
fails on 6 of 8 symbols. These instruments carry a persistent upward drift;
the edge here is specifically "buy dips in a rising market," not a symmetric
FVG effect.

**Q: Why H4/H1 structure and not the daily chart?**
A: Daily structure flips too late. In a real loss cluster, 3 of 5 consecutive
Gold losses fired while daily structure still read bullish — the first leg
down off a top always precedes a confirmed daily reversal. H4/H1 structure,
using the LuxAlgo close-crosses-pivot definition, turns much sooner.

**Q: The M15 chart looks wrong / the numbers don't match what I expect for old
trades.**
A: Check the date. Before ~2021-07-16 this MT5 terminal doesn't have real
intraday history cached — it returns one fake bar per day instead of erroring.
See [Known Limitations](#known-limitations--failed-ideas).

**Q: What is "1 unit" / "1 lot" in the fixed-size sizing mode?**
A: A real MT5 lot, converted via the symbol's actual `trade_contract_size`
(Gold: 100 oz/lot, FX pairs: 100,000 units/lot, index CFDs: 1 unit/lot) — so
0.10 lot means the same thing here as it would on your broker's order ticket.

**Q: Why does percent-risk sizing outperform fixed lot size?**
A: Losing trades systematically carry wider stops than winners (a wide ATR
stop means a volatile setup that both fails more often and costs more when it
does). Percent-risk shrinks the position exactly when the stop is wide, which
keeps the realized R-multiple ratio close to the R:R you set. Fixed lots don't
adjust, so a nominal 2:1 pays closer to 1.4:1 in dollars.

**Q: Why does the win rate go up when I lower the reward:risk ratio, but the
final equity goes down?**
A: A closer target is mechanically easier to hit — that's the whole lever, not
a real improvement. Judge any change by profit factor and final equity, never
win rate in isolation.

**Q: Can I trust the H4 bias filter's numbers?**
A: With one real caveat: H4/H1 history on this terminal only goes back to
~2021-07, so every number for that filter covers roughly 5 years, not the
full 8+ used for the long-only baseline. It was cross-checked against random
trade removal of the same size to confirm it's real selection, not just
trading less.

**Q: Is this a finished, tradeable system?**
A: No. It doesn't model spread, slippage, or real order queueing, every result
is in-sample on one broker's history, and several per-symbol defaults were
chosen by sweeping this same data. Treat it as a research tool for narrowing
down ideas, not a signal source.

---

## Disclaimer

This is a private research tool, not a published or distributed trading
product. It never places, modifies, or cancels a live order — every result
above comes from historical simulation against data already cached in a local
MetaTrader 5 terminal. Past backtested performance is not indicative of future
results, and every number in this document is in-sample. Nothing here is
financial advice.
