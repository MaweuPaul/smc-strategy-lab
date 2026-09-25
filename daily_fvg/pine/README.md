# Daily FVG, TradingView Scripts

Pine v5 ports of the Daily FVG Retracement rule, so a setting that tested well
in this repo can be watched on a live chart without re-marking the levels by
eye. They are the answer to the Pine Script item on the main
[Roadmap](../../README.md#roadmap--to-be-done).

> These place orders in TradingView's simulator only. Nothing in this
> repository connects to a broker, and these files do not change that.

---

## The four files

Each direction has a matched pair: an indicator to watch with, a strategy to
measure with.

| File | Type | Direction | Status |
|---|---|---|---|
| `daily_fvg_long_indicator.pine` | Indicator | Long | Validated entries |
| `daily_fvg_long_strategy.pine` | Strategy | Long | Validated entries |
| `daily_fvg_short_indicator.pine` | Indicator | Short | **Lost money on every symbol tested.** Research only. |
| `daily_fvg_short_strategy.pine` | Strategy | Short | **Lost money on every symbol tested.** Research only. |

All four run on the **daily** chart and will say so on the chart if you load
them anywhere else.

### Why an indicator AND a strategy

Pine forces the split: a script is either `indicator()` or `strategy()`, never
both, so chart drawings and Strategy Tester P&L cannot come from one file.

Use the **indicator** on a chart you actually watch. It is lighter, it does not
clutter the chart with simulated trade arrows, and it is the one carrying the
alert that fires the evening a gap is touched. Use the **strategy** when you
want numbers: profit factor, drawdown, position sizing and commission are
things only a `strategy()` can model.

The consequence is that the gap-detection block is duplicated by hand across
all four files. Pine can only share code through a `library()` published to
TradingView, which a private repo cannot do, so the duplication is deliberate
rather than an oversight. **A change to the detection logic has to be applied
to all four.** Each file says so in its header. The short files mirror the
logic rather than copying it, with every comparison inverted, so port the
intent rather than the characters.

---

## Getting started

1. Open a daily chart. Gold (`XAUUSD`) is where this rule is strongest.
2. Paste the file into TradingView's Pine editor and add it to the chart.
3. Read the label at the right edge first. It states the date your bias
   timeframe actually begins, which is the real start of the usable window.
4. Open the settings panel in the top right for the live bias state and the
   current configuration.

---

## Reading the chart

| What you see | What it means |
|---|---|
| Teal box | A bullish daily FVG waiting to be revisited. It extends right until touched or invalidated. |
| BUY label | An entry at that day's open, one day after the gap was touched. |
| Blue / red / green lines | Entry, stop and target, drawn until the trade resolves. |
| SL / TP label | How the trade ended. |
| **Red background** | The bias filter is refusing to trade: structure is against you. |
| **Grey background** | The bias filter could not be evaluated, because the bias timeframe has no data that far back. |

The two background colours mean different things on purpose. A quiet stretch
should always be visibly a decision rather than a missing drawing.

The short scripts invert this: SELL instead of BUY, and the background tints
**green** when structure is bullish and the filter is refusing to sell. Grey
still means the bias could not be evaluated.

---

## Settings are a tolerance dial

The shipped values are a starting point, not a recommendation. The same rule
can be made to trade often with deep drawdowns, or rarely with shallow ones,
and neither is objectively correct.

| Input | Effect of increasing it |
|---|---|
| **Stop buffer (x ATR)** | Wider stop: fewer trades, each risking more distance, with noticeably shallower drawdown |
| **Target (R:R)** | Further target: lower win rate, higher average R, wider error bars |
| **Risk per trade (%)** | Bigger swings in both directions, in proportion |
| **Pivot lookback** | Slower bias: fewer flips, later turns |

Two measured configurations, both filtered on D1 bias at 2% risk, from
2021-07-15:

| Config | XAUUSD | XAGUSD |
|---|---|---|
| 0.25x buffer / 2R (shipped) | +0.558R, PF 2.14, DD -19.5% | +0.227R, PF 1.37, DD -17.3% |
| 1.0x buffer / 3R | +0.721R, PF 2.23, DD **-6.3%** | +0.716R, PF 2.34, DD **-7.7%** |

The wider-stop version measured better on both metals with roughly a third of
the drawdown, at the cost of about a third of the trades. Try it before
assuming the shipped values are right for you.

Tune until the drawdown is one you could actually sit through. A rule
abandoned halfway through a drawdown captures the losses and none of the
recovery, which is worse than never having traded it.

---

## What has been verified

The detection logic was checked by transliterating the Pine control flow back
into Python and running it against `find_entries()` in `daily_fvg_newday.py`:

| Symbol | Engine triggers | Pine triggers | Matched | Differences | Max stop difference |
|---|---|---|---|---|---|
| EURUSD | 220 | 220 | 220 | 0 | 0.00e+00 |
| XAUUSD | 272 | 272 | 272 | 0 | 0.00e+00 |
| USTEC | 324 | 324 | 324 | 0 | 0.00e+00 |

Every gap, every trigger day and every stop price agreed exactly. That covers
the part where look-ahead bias would live: which gaps are found, when they are
considered dead, and which day arms an entry.

It does **not** cover the exits. The Python engine resolves those on M15 while
these run on daily bars, so P&L will differ. See
[the main strategy README](../README.md#tradingview-indicator--strategy) for
what to expect and why.

---

## The short scripts

`daily_fvg_short_indicator.pine` and `daily_fvg_short_strategy.pine` are the exact
mirror: bearish gaps, shorts at the next day's open, stop above the gap, and
they only fire while structure reads bearish. They cover precisely the
stretches the long scripts sit out. The background tints **green** when the
filter is refusing to sell, the inverse of the long files' red.

It lost money on every symbol tested. At the shipped settings, from
2021-07-15:

| Symbol | Short + bearish bias | Long + bullish bias |
|---|---|---|
| XAUUSD | -0.048R, PF 0.88 | +0.790R, PF 2.36 |
| XAGUSD | -0.231R, PF 0.71 | +0.282R, PF 1.42 |
| USTEC | -0.261R, PF 0.68 | +0.043R, PF 1.08 |

A closer target is the one thing that measurably helps. At 1:1 with H4 bias,
gold wins 52.5% of its shorts and avg R rises from -0.377R to -0.037R. It
still does not pay, and the reason is worth understanding: at 1:1 the spread
and commission consume most of each win, so a higher hit rate is bought back
out at the same time. The single positive cell found anywhere is gold on D1
bias at 1.5R, worth +1.3% over five years with a 15.6% drawdown, on 30 trades,
with an error bar twice the size of the result. That is noise.

This is not a tuning problem. `daily_fvg_bearish.py` and
`daily_fvg_bearish_ltf_bias.py` reached the same verdict independently at
different parameters. If "any imbalance is a magnet" were symmetrically true,
shorting bearish gaps should work about as well as buying bullish ones. It
does not, which is evidence that the gap is not what carries the edge on the
long side; upward drift in the instrument is, and a short has no such
tailwind.

Use it to see the bearish setups and to check whether the long script is right
to stand aside. Do not trade it on the strength of anything measured here.

---

## Known limitations

- **Daily bars only**, so exits are coarser than the Python engine's M15.
- **Exit orders go live the bar after the fill**, so a stop or target reached
  on the entry day itself is handled a day late.
- **Position size is computed from the trigger day's close**, because the real
  fill price (the next day's open) is not knowable when the order is
  submitted. The Python engine sizes off the actual fill.
- **The bias filter is gated at order submission**, one bar before
  `daily_fvg_ltf_bias.py` reads it.
- **Do not disable the date range on a symbol whose history reaches the
  1800s.** Prices there are a few cents and risk-based sizing buys absurd
  quantities. An early run of this reported +103,956% that way.

---

## Disclaimer

Backtested results are not a promise. Every figure quoted here is in-sample,
measured on one broker's data over about five years, with sample sizes between
25 and 90 trades. Past performance, backtested or otherwise, is not indicative
of future results. Nothing here is financial advice. If you trade any of it,
do so at your own risk, with money you are fully prepared to lose.
