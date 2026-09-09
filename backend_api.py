"""
FastAPI backend for the React ICT backtest UI.

Exposes:
  GET  /api/symbols               -- list of tradeable symbols on this MT5 account
  POST /api/backtest               -- run the HTF->LTF backtest, returns trades + metrics
  POST /api/asian-backtest         -- run the Asian-session sweep/continuation backtest
  POST /api/orb-backtest           -- run the Opening Range Breakout backtest
  POST /api/daily-ob-backtest      -- run the Daily Order Block retracement backtest
  POST /api/daily-fvg-backtest     -- run the Daily FVG retracement backtest
  GET  /api/candles                -- OHLC candles for a symbol/timeframe/time range (for replay)

Data is read-only from a running MetaTrader5 terminal. No orders are ever placed.
Run with:  uvicorn backend_api:app --port 8001 --reload
"""

from datetime import datetime, timezone
from typing import List, Optional, Literal
from uuid import uuid4

import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from htf_ltf_backtest import run, HTF_TO_LTF, TIMEFRAMES, DEFAULT_KILLZONES
from asian_session_backtest import run_asian
from orb_backtest import run_orb, run_orb_ny_open
from daily_ob_backtest import run_daily_ob
from daily_ob_support import account_trades, TF_MINUTES
from p404_sweep_reversal import compute_atr14
import daily_fvg_newday as fvg_mod
from daily_fvg_resistance_filter import filter_by_resistance
from daily_fvg_continuation import find_continuation_entries
from daily_fvg_ride_to_magnet import find_leg1_short_entries, simulate_bidirectional
from smc_structure import compute_structure, bias_at, BULLISH as SMC_BULLISH

app = FastAPI(title="ICT HTF/LTF Backtest API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5180", "http://127.0.0.1:5180",
                   "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ALL_SYMBOLS = ["EURUSD", "USDJPY", "XAUUSD", "GBPUSD", "AUDUSD", "USDCHF", "NZDUSD", "USDCAD",
               "XAGUSD", "USTEC", "US30", "US500", "UK100", "DXY"]


class BacktestRequest(BaseModel):
    symbols: List[str]
    htf: str = "H4"
    htf_bars: int = 3000
    min_rr: float = 3.0
    max_rr: float = 5.0
    risk_pct: float = 1.0
    start_equity: float = 10_000.0
    london: List[int] = [7, 10]
    ny: List[int] = [12, 15]
    pyramiding: bool = False
    max_pyramid_legs: int = 3
    pyramid_risk_mult: float = 0.5


class AsianBacktestRequest(BaseModel):
    symbols: List[str]
    ltf: str = "M15"
    bars: int = 20000
    asian_start: int = 0
    asian_end: int = 6
    min_rr: float = 3.0
    max_rr: float = 5.0
    risk_pct: float = 1.0
    start_equity: float = 10_000.0
    mode_filter: str = "all"  # "all" | "reversal" | "continuation"
    htf_bias_filter: Optional[str] = None  # None (off) | "H4" | "D1"
    htf_bias_bars: int = 3000
    allow_neutral_bias: bool = True


class OrbBacktestRequest(BaseModel):
    symbols: List[str]
    bars: int = 1_800_000  # M1 bars
    session_open_hour: int = 7
    session_open_minute: int = 0
    range_minutes: int = 30
    target_mode: str = "measured_move"  # "measured_move" | "rr"
    target_range_mult: float = 1.0
    min_rr: float = 1.5
    max_rr: float = 3.0
    max_hold_bars: int = 4320
    use_daily_bias: bool = True
    risk_pct: float = 1.0
    start_equity: float = 10_000.0


class OrbNyOpenBacktestRequest(BaseModel):
    symbols: List[str]
    bars: int = 700_000  # M1 bars
    candle_minutes: Literal[1, 5] = 5
    monitor_hours: float = 6.0
    target_mode: str = "measured_move"  # "measured_move" | "rr"
    target_range_mult: float = 1.0
    min_rr: float = 1.5
    max_rr: float = 3.0
    max_hold_bars: int = 4320
    use_daily_bias: bool = True
    risk_pct: float = 1.0
    start_equity: float = 10_000.0


class DailyObBacktestRequest(BaseModel):
    symbols: List[str] = Field(min_length=1, max_length=50)
    htf_bars: int = Field(default=2600, ge=20, le=50000)
    htf_timeframe: Literal["D1", "H4"] = "D1"
    swing_window: int = Field(default=3, ge=1, le=20)
    min_rr: float = Field(default=3.0, gt=0, le=100, allow_inf_nan=False)
    max_rr: float = Field(default=5.0, gt=0, le=100, allow_inf_nan=False)
    use_killzones: bool = False
    bias_mode: Literal["off", "filter", "reverse"] = "off"
    require_displacement: bool = True
    use_invalidation_exit: bool = True
    invalidation_minor_window: int = Field(default=2, ge=1, le=10)
    invalidation_confirm_bars: int = Field(default=1, ge=1, le=20)
    invalidation_grace_bars: int = Field(default=10, ge=0, le=1000)
    pyramid_trigger_r: Optional[float] = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    pyramid_risk_mult: float = Field(default=0.5, gt=0, le=1, allow_inf_nan=False)
    risk_pct: float = Field(default=1.0, gt=0, le=5, allow_inf_nan=False)
    start_equity: float = Field(default=10_000.0, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_settings(self):
        if self.min_rr > self.max_rr:
            raise ValueError("min_rr must not exceed max_rr")
        if any(not sym.strip() for sym in self.symbols) or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Symbols must be nonempty and unique")
        return self


class DailyFvgBacktestRequest(BaseModel):
    symbol: str
    months_back: int = Field(default=96, ge=1, le=300)
    target_rr: float = Field(default=2.0, gt=0, le=20)
    atr_buffer_mult: float = Field(default=0.25, ge=0, le=3)
    use_resistance_filter: bool = False
    entry_style: Literal["retracement", "continuation"] = "retracement"
    use_magnet_leg: bool = False
    magnet_leg_risk_mult: float = Field(default=0.5, ge=0.1, le=1.0)
    start_equity: float = Field(default=100_000.0, gt=0)
    risk_pct: float = Field(default=1.5, gt=0, le=10)
    sizing_mode: Literal["risk_pct", "fixed_units"] = "risk_pct"
    fixed_lots: float = Field(default=0.10, gt=0)
    use_ltf_bias: bool = False
    ltf_bias_timeframe: Literal["H4", "H1"] = "H4"
    ltf_bias_size: int = Field(default=5, ge=2, le=50)


def reliable_intraday_start(df):
    """First timestamp from which an intraday series is actually intraday.

    The MT5 python API only returns bars the terminal has cached locally.
    For older history it silently returns ONE degenerate bar per day whose
    OHLC is just that day's range, instead of erroring -- so an H4/M15
    request can come back looking like daily data. Structure or execution
    computed on that is meaningless, so callers need to know where the
    real intraday data begins.

    Detects it by bar density: compares each day's bar count against the
    recent typical count and returns the day after the last sparse one.
    """
    counts = df.groupby(df["time"].dt.date).size()
    if counts.empty:
        return None
    typical = counts.tail(60).median()
    if not typical or typical < 2:
        return None  # even recent data isn't intraday; caller decides
    threshold = max(2.0, typical * 0.5)

    # Look for a SUSTAINED run of sparse days, not isolated ones -- half
    # days, holidays and the current partial day are legitimately sparse
    # and must not be mistaken for the historical gap, which shows up as
    # months of consecutive one-bar days.
    MIN_RUN = 10
    sparse = (counts < threshold).values
    dates = list(counts.index)
    last_gap_end = None
    run_start = None
    for i, is_sparse in enumerate(sparse):
        if is_sparse and run_start is None:
            run_start = i
        elif not is_sparse and run_start is not None:
            if i - run_start >= MIN_RUN:
                last_gap_end = i - 1
            run_start = None
    if run_start is not None and len(sparse) - run_start >= MIN_RUN:
        last_gap_end = len(sparse) - 1

    if last_gap_end is None:
        return df["time"].iloc[0]
    if last_gap_end >= len(dates) - 1:
        return None  # the sparse run reaches the end -- nothing usable
    after = df[df["time"].dt.date > dates[last_gap_end]]
    return after["time"].iloc[0] if len(after) else None


def get_symbol_info(symbol: str):
    """Fresh init/select/shutdown cycle, same pattern as fvg_mod.fetch() --
    reading symbol_info() on a connection fetch() already shut down
    returns a disconnected-default (e.g. contract_size 1.0) with no error."""
    if not mt5.initialize():
        raise HTTPException(404, f"MT5 initialize failed: {mt5.last_error()}")
    if not mt5.symbol_select(symbol, True):
        mt5.shutdown()
        raise HTTPException(404, f"Could not select symbol {symbol}")
    info = mt5.symbol_info(symbol)
    mt5.shutdown()
    if info is None:
        raise HTTPException(404, f"Symbol {symbol} not found")
    return info


def compute_cagr(combined: pd.DataFrame, final_equity: float, start_equity: float,
                 evaluation_start=None, evaluation_end=None):
    """Annualize the supplied balance over an explicit data observation period.

    Other engines retain their legacy trade-span fallback. Annualization
    does not correct execution assumptions or unrealistic position capacity.
    """
    try:
        start = pd.Timestamp(evaluation_start) if evaluation_start is not None else pd.to_datetime(combined.entry_time).min()
        end = pd.Timestamp(evaluation_end) if evaluation_end is not None else pd.to_datetime(combined.exit_time).max()
        years = (end - start).total_seconds() / (365.25 * 86400)
        if years <= 0 or start_equity <= 0 or final_equity <= 0:
            return None, years
        value = ((final_equity / start_equity) ** (1 / years) - 1) * 100
        return (float(value) if np.isfinite(value) else None), float(years)
    except (ValueError, OverflowError, TypeError):
        return None, None


def _clean(obj):
    """Recursively make a JSON-safe structure (NaN/NaT -> None, Timestamps -> ISO strings)."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, datetime)):
        return None if pd.isna(obj) else obj.isoformat()
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if pd.isna(obj) if not isinstance(obj, (list, dict)) else False:
        return None
    return obj


@app.get("/api/symbols")
def symbols():
    return {"symbols": ALL_SYMBOLS, "htf_to_ltf": HTF_TO_LTF}


@app.post("/api/backtest")
def backtest(req: BacktestRequest):
    if not req.symbols:
        raise HTTPException(400, "Pick at least one symbol")
    if req.htf not in HTF_TO_LTF:
        raise HTTPException(400, f"htf must be one of {list(HTF_TO_LTF.keys())}")

    killzones = [tuple(req.london), tuple(req.ny)]
    all_trades = []
    errors = {}
    for sym in req.symbols:
        try:
            trades = run(sym, req.htf, req.htf_bars, killzones,
                         min_rr=req.min_rr, max_rr=req.max_rr,
                         pyramiding=req.pyramiding, max_pyramid_legs=req.max_pyramid_legs,
                         pyramid_risk_mult=req.pyramid_risk_mult)
            if not trades.empty:
                trades["symbol"] = sym
                all_trades.append(trades)
        except Exception as e:
            errors[sym] = str(e)

    if not all_trades:
        return {"trades": [], "metrics": None, "per_symbol": {}, "errors": errors, "ltf": HTF_TO_LTF[req.htf]}

    combined = pd.concat(all_trades, ignore_index=True).sort_values("entry_time").reset_index(drop=True)

    risk_mults = combined["risk_mult"] if "risk_mult" in combined.columns else pd.Series([1.0] * len(combined))
    equity = [req.start_equity]
    for r, rm in zip(combined["r_multiple"], risk_mults):
        equity.append(equity[-1] * (1 + req.risk_pct / 100 * rm * r))
    equity = np.array(equity)
    dd = (equity - np.maximum.accumulate(equity)) / np.maximum.accumulate(equity)

    wins = combined[combined.r_multiple > 0]
    losses = combined[combined.r_multiple <= 0]
    pf = wins.r_multiple.sum() / abs(losses.r_multiple.sum()) if len(losses) else None

    cagr_pct, years_span = compute_cagr(combined, float(equity[-1]), req.start_equity)
    metrics = {
        "trades": len(combined),
        "win_rate": float(len(wins) / len(combined) * 100),
        "avg_r": float(combined.r_multiple.mean()),
        "profit_factor": pf,
        "final_equity": float(equity[-1]),
        "max_drawdown_pct": float(dd.min() * 100),
        "cagr_pct": cagr_pct,
        "years_span": years_span,
        "equity_curve": equity.tolist(),
    }

    per_symbol = {}
    for sym, g in combined.groupby("symbol"):
        w = g[g.r_multiple > 0]
        per_symbol[sym] = {
            "trades": len(g),
            "win_rate": float(len(w) / len(g) * 100),
            "avg_r": float(g.r_multiple.mean()),
        }

    combined = combined.reset_index().rename(columns={"index": "id"})
    trades_records = _clean(combined.to_dict(orient="records"))

    return {
        "trades": trades_records,
        "metrics": metrics,
        "per_symbol": per_symbol,
        "errors": errors,
        "ltf": HTF_TO_LTF[req.htf],
    }


@app.post("/api/asian-backtest")
def asian_backtest(req: AsianBacktestRequest):
    if not req.symbols:
        raise HTTPException(400, "Pick at least one symbol")
    if req.ltf not in TIMEFRAMES:
        raise HTTPException(400, f"ltf must be one of {list(TIMEFRAMES.keys())}")
    if req.mode_filter not in ("all", "reversal", "continuation"):
        raise HTTPException(400, "mode_filter must be 'all', 'reversal', or 'continuation'")

    all_trades = []
    errors = {}
    for sym in req.symbols:
        try:
            trades = run_asian(sym, req.ltf, req.bars,
                                asian_hours=(req.asian_start, req.asian_end),
                                min_rr=req.min_rr, max_rr=req.max_rr,
                                htf_name=req.htf_bias_filter, htf_bars=req.htf_bias_bars,
                                allow_neutral_bias=req.allow_neutral_bias)
            if not trades.empty:
                trades["symbol"] = sym
                all_trades.append(trades)
        except Exception as e:
            errors[sym] = str(e)

    if not all_trades:
        return {"trades": [], "metrics": None, "per_symbol": {}, "per_mode": {}, "errors": errors, "ltf": req.ltf}

    combined = pd.concat(all_trades, ignore_index=True).sort_values("entry_time").reset_index(drop=True)
    if req.mode_filter != "all":
        combined = combined[combined["mode"] == req.mode_filter].reset_index(drop=True)
    if combined.empty:
        return {"trades": [], "metrics": None, "per_symbol": {}, "per_mode": {}, "errors": errors, "ltf": req.ltf}

    equity = [req.start_equity]
    for r in combined["r_multiple"]:
        equity.append(equity[-1] * (1 + req.risk_pct / 100 * r))
    equity = np.array(equity)
    dd = (equity - np.maximum.accumulate(equity)) / np.maximum.accumulate(equity)

    wins = combined[combined.r_multiple > 0]
    losses = combined[combined.r_multiple <= 0]
    pf = wins.r_multiple.sum() / abs(losses.r_multiple.sum()) if len(losses) else None

    cagr_pct, years_span = compute_cagr(combined, float(equity[-1]), req.start_equity)
    metrics = {
        "trades": len(combined),
        "win_rate": float(len(wins) / len(combined) * 100),
        "avg_r": float(combined.r_multiple.mean()),
        "profit_factor": pf,
        "final_equity": float(equity[-1]),
        "max_drawdown_pct": float(dd.min() * 100),
        "cagr_pct": cagr_pct,
        "years_span": years_span,
        "equity_curve": equity.tolist(),
    }

    per_symbol = {}
    for sym, g in combined.groupby("symbol"):
        w = g[g.r_multiple > 0]
        per_symbol[sym] = {"trades": len(g), "win_rate": float(len(w) / len(g) * 100), "avg_r": float(g.r_multiple.mean())}

    per_mode = {}
    for mode, g in combined.groupby("mode"):
        w = g[g.r_multiple > 0]
        per_mode[mode] = {"trades": len(g), "win_rate": float(len(w) / len(g) * 100), "avg_r": float(g.r_multiple.mean())}

    # give the frontend the fields TradeReplay expects: reuse the Asian range as the "POI" zone
    combined["poi_top"] = combined["asian_high"]
    combined["poi_bottom"] = combined["asian_low"]
    combined["poi_type"] = combined["mode"]
    combined["window_start_time"] = pd.to_datetime(combined["day"], utc=True)

    combined = combined.reset_index().rename(columns={"index": "id"})
    trades_records = _clean(combined.to_dict(orient="records"))

    return {
        "trades": trades_records,
        "metrics": metrics,
        "per_symbol": per_symbol,
        "per_mode": per_mode,
        "errors": errors,
        "ltf": req.ltf,
    }


@app.post("/api/orb-backtest")
def orb_backtest(req: OrbBacktestRequest):
    if not req.symbols:
        raise HTTPException(400, "Pick at least one symbol")
    if req.target_mode not in ("measured_move", "rr"):
        raise HTTPException(400, "target_mode must be 'measured_move' or 'rr'")

    all_trades = []
    errors = {}
    for sym in req.symbols:
        try:
            trades = run_orb(sym, req.bars, req.session_open_hour, req.session_open_minute,
                              req.range_minutes, min_rr=req.min_rr, max_rr=req.max_rr,
                              max_hold_bars=req.max_hold_bars, target_mode=req.target_mode,
                              target_range_mult=req.target_range_mult, use_daily_bias=req.use_daily_bias)
            if not trades.empty:
                trades["symbol"] = sym
                all_trades.append(trades)
        except Exception as e:
            errors[sym] = str(e)

    if not all_trades:
        return {"trades": [], "metrics": None, "per_symbol": {}, "errors": errors, "ltf": "M1"}

    combined = pd.concat(all_trades, ignore_index=True).sort_values("entry_time").reset_index(drop=True)

    risk_mults = combined["risk_mult"] if "risk_mult" in combined.columns else pd.Series([1.0] * len(combined))
    equity = [req.start_equity]
    for r, rm in zip(combined["r_multiple"], risk_mults):
        equity.append(equity[-1] * (1 + req.risk_pct / 100 * rm * r))
    equity = np.array(equity)
    dd = (equity - np.maximum.accumulate(equity)) / np.maximum.accumulate(equity)

    wins = combined[combined.r_multiple > 0]
    losses = combined[combined.r_multiple <= 0]
    pf = wins.r_multiple.sum() / abs(losses.r_multiple.sum()) if len(losses) else None

    cagr_pct, years_span = compute_cagr(combined, float(equity[-1]), req.start_equity)
    metrics = {
        "trades": len(combined),
        "win_rate": float(len(wins) / len(combined) * 100),
        "avg_r": float(combined.r_multiple.mean()),
        "profit_factor": pf,
        "final_equity": float(equity[-1]),
        "max_drawdown_pct": float(dd.min() * 100),
        "cagr_pct": cagr_pct,
        "years_span": years_span,
        "equity_curve": equity.tolist(),
    }

    per_symbol = {}
    for sym, g in combined.groupby("symbol"):
        w = g[g.r_multiple > 0]
        per_symbol[sym] = {"trades": len(g), "win_rate": float(len(w) / len(g) * 100), "avg_r": float(g.r_multiple.mean())}

    # give the frontend the fields TradeReplay expects: reuse the opening range as the "POI" zone
    combined["poi_top"] = combined["range_high"]
    combined["poi_bottom"] = combined["range_low"]
    combined["poi_type"] = combined["direction"]
    combined["window_start_time"] = pd.to_datetime(combined["range_start"], utc=True)

    combined = combined.reset_index().rename(columns={"index": "id"})
    trades_records = _clean(combined.to_dict(orient="records"))

    return {
        "trades": trades_records,
        "metrics": metrics,
        "per_symbol": per_symbol,
        "errors": errors,
        "ltf": "M1",
    }


@app.post("/api/orb-ny-open-backtest")
def orb_ny_open_backtest(req: OrbNyOpenBacktestRequest):
    if not req.symbols:
        raise HTTPException(400, "Pick at least one symbol")
    if req.target_mode not in ("measured_move", "rr"):
        raise HTTPException(400, "target_mode must be 'measured_move' or 'rr'")

    all_trades = []
    errors = {}
    for sym in req.symbols:
        try:
            trades = run_orb_ny_open(sym, req.bars, req.candle_minutes, req.monitor_hours,
                                      min_rr=req.min_rr, max_rr=req.max_rr,
                                      max_hold_bars=req.max_hold_bars, target_mode=req.target_mode,
                                      target_range_mult=req.target_range_mult, use_daily_bias=req.use_daily_bias)
            if not trades.empty:
                trades["symbol"] = sym
                all_trades.append(trades)
        except Exception as e:
            errors[sym] = str(e)

    if not all_trades:
        return {"trades": [], "metrics": None, "per_symbol": {}, "errors": errors, "ltf": "M1"}

    combined = pd.concat(all_trades, ignore_index=True).sort_values("entry_time").reset_index(drop=True)

    risk_mults = combined["risk_mult"] if "risk_mult" in combined.columns else pd.Series([1.0] * len(combined))
    equity = [req.start_equity]
    for r, rm in zip(combined["r_multiple"], risk_mults):
        equity.append(equity[-1] * (1 + req.risk_pct / 100 * rm * r))
    equity = np.array(equity)
    dd = (equity - np.maximum.accumulate(equity)) / np.maximum.accumulate(equity)

    wins = combined[combined.r_multiple > 0]
    losses = combined[combined.r_multiple <= 0]
    pf = wins.r_multiple.sum() / abs(losses.r_multiple.sum()) if len(losses) else None

    cagr_pct, years_span = compute_cagr(combined, float(equity[-1]), req.start_equity)
    metrics = {
        "trades": len(combined),
        "win_rate": float(len(wins) / len(combined) * 100),
        "avg_r": float(combined.r_multiple.mean()),
        "profit_factor": pf,
        "final_equity": float(equity[-1]),
        "max_drawdown_pct": float(dd.min() * 100),
        "cagr_pct": cagr_pct,
        "years_span": years_span,
        "equity_curve": equity.tolist(),
    }

    per_symbol = {}
    for sym, g in combined.groupby("symbol"):
        w = g[g.r_multiple > 0]
        per_symbol[sym] = {"trades": len(g), "win_rate": float(len(w) / len(g) * 100), "avg_r": float(g.r_multiple.mean())}

    combined["poi_top"] = combined["range_high"]
    combined["poi_bottom"] = combined["range_low"]
    combined["poi_type"] = combined["direction"]
    combined["window_start_time"] = pd.to_datetime(combined["range_start"], utc=True)

    combined = combined.reset_index().rename(columns={"index": "id"})
    trades_records = _clean(combined.to_dict(orient="records"))

    return {
        "trades": trades_records,
        "metrics": metrics,
        "per_symbol": per_symbol,
        "errors": errors,
        "ltf": "M1",
    }


@app.post("/api/daily-ob-backtest")
def daily_ob_backtest(req: DailyObBacktestRequest):
    if not req.symbols:
        raise HTTPException(400, "Pick at least one symbol")

    killzones = DEFAULT_KILLZONES if req.use_killzones else None
    is_h4 = req.htf_timeframe == "H4"
    ltf_name = "M15" if is_h4 else "H1"
    monitor_days = 360 if is_h4 else 60  # bars on htf_timeframe to keep a zone "live"
    max_hold_bars = 1600 if is_h4 else 400  # bars on ltf_name before a trade times out
    all_trades = []
    errors = {}
    coverage = {}
    as_of = pd.Timestamp.now(tz="UTC")
    metadata = {"run_id": str(uuid4()), "engine_version": "daily-ob-causal-20260907",
                "config": req.model_dump(), "as_of": as_of.isoformat(), "ltf": ltf_name,
                "execution_model": "next-open entries/invalidation; gap fills; stop-first OHLC",
                "cost_model": "gross prices: excludes spread, commission, slippage and financing"}
    for sym in req.symbols:
        try:
            trades = run_daily_ob(sym, req.htf_bars, req.swing_window,
                                   killzones=killzones, min_rr=req.min_rr, max_rr=req.max_rr,
                                   bias_mode=req.bias_mode, require_displacement=req.require_displacement,
                                   use_invalidation_exit=req.use_invalidation_exit,
                                   invalidation_minor_window=req.invalidation_minor_window,
                                   invalidation_confirm_bars=req.invalidation_confirm_bars,
                                   invalidation_grace_bars=req.invalidation_grace_bars,
                                   pyramid_trigger_r=req.pyramid_trigger_r,
                                   pyramid_risk_mult=req.pyramid_risk_mult,
                                   htf_name=req.htf_timeframe, ltf_name=ltf_name,
                                   monitor_days=monitor_days, max_hold_bars=max_hold_bars, as_of=as_of)
            if trades.attrs.get("evaluation_start") is not None:
                coverage[sym] = dict(trades.attrs)
            if not trades.empty:
                trades["symbol"] = sym
                all_trades.append(trades)
        except Exception as e:
            errors[sym] = str(e)

    if not all_trades:
        return _clean({**metadata, "trades": [], "metrics": None, "per_symbol": {}, "errors": errors, "coverage": coverage})

    combined, ledger = account_trades(pd.concat(all_trades, ignore_index=True), req.risk_pct, req.start_equity)
    evaluation_start = min(c["evaluation_start"] for c in coverage.values())
    evaluation_end = max(c["evaluation_end"] for c in coverage.values())
    cagr_pct, years_span = compute_cagr(combined, ledger["final_equity"], req.start_equity,
                                      evaluation_start, evaluation_end)
    metrics = {**ledger, "trades": len(combined),
               "win_rate": float((combined.r_multiple > 0).mean() * 100),
               "avg_r": float(combined.r_multiple.mean()),
               "cagr_pct": cagr_pct, "years_span": years_span,
               "evaluation_start": evaluation_start, "evaluation_end": evaluation_end}

    per_symbol = {}
    for sym, g in combined.groupby("symbol"):
        w = g[g.r_multiple > 0]
        per_symbol[sym] = {"trades": len(g), "win_rate": float(len(w) / len(g) * 100), "avg_r": float(g.r_multiple.mean())}

    # give the frontend the fields TradeReplay expects: reuse the order block as the "POI" zone
    combined["poi_top"] = combined["ob_top"]
    combined["poi_bottom"] = combined["ob_bottom"]
    combined["poi_type"] = combined["ob_type"]
    combined["window_start_time"] = pd.to_datetime(combined["ob_formed_time"], utc=True)

    combined = combined.reset_index().rename(columns={"index": "id"})
    trades_records = _clean(combined.to_dict(orient="records"))

    return _clean({
        **metadata, "coverage": coverage,
        "trades": trades_records,
        "metrics": metrics,
        "per_symbol": per_symbol,
        "errors": errors,
        "ltf": ltf_name,
    })



@app.post("/api/daily-fvg-backtest")
def daily_fvg_backtest(req: DailyFvgBacktestRequest):
    symbol = req.symbol
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = (pd.Timestamp(end) - pd.DateOffset(months=req.months_back)).to_pydatetime()

    try:
        d1 = fvg_mod.fetch(symbol, mt5.TIMEFRAME_D1, start, end)
        m15 = fvg_mod.fetch(symbol, mt5.TIMEFRAME_M15, start, end)
    except RuntimeError as e:
        raise HTTPException(404, str(e))

    fvg_mod.START_EQUITY = req.start_equity  # module-level knob; fine for a single-user local dev tool
    fvg_mod.RISK_PCT = req.risk_pct / 100.0
    fvg_mod.SIZING_MODE = req.sizing_mode
    # "fixed_lots" is real MT5 lots (e.g. 0.10) -- convert to the raw
    # underlying-quantity "units" this backtest's P&L math actually uses
    # (qty * price_change = $) via each symbol's own contract size, so
    # 0.10 lot means the same real-world size on Gold (100oz/lot) as it
    # does on EURUSD (100,000 units/lot) or an index (1 unit/lot).
    # NB: fvg_mod.fetch() shuts the MT5 connection down when it's done, so
    # symbol_info must be read through its own fresh init/select/shutdown
    # cycle -- reading it after the fetches above silently returned a
    # disconnected-default contract_size of 1.0 instead of erroring.
    symbol_info = get_symbol_info(symbol)
    contract_size = symbol_info.trade_contract_size
    fvg_mod.FIXED_UNITS = req.fixed_lots * contract_size
    atr = compute_atr14(d1).values
    if req.entry_style == "continuation":
        entries = find_continuation_entries(d1, m15)
    else:
        entries = fvg_mod.find_entries(d1, m15)
    for _, gap in entries:
        gap["_atr"] = atr[gap["formed_at"]] if not np.isnan(atr[gap["formed_at"]]) else 0.0
    fvg_mod.TARGET_RR = req.target_rr  # module-level knobs; fine for a single-user local dev tool
    fvg_mod.ATR_BUFFER_MULT = req.atr_buffer_mult

    # resistance filter only tested/valid for the retracement style -- it
    # needs gap["_entered_day_idx"], which continuation entries never set
    n_skipped_resistance = 0
    if req.use_resistance_filter and req.entry_style == "retracement":
        entries, n_skipped_resistance = filter_by_resistance(d1, entries)

    # Lower-timeframe BOS/CHoCH bias: keep only entries where H4 (or H1)
    # structure reads bullish at the moment the trade fires. Bias is read
    # strictly BEFORE the entry bar's timestamp, so nothing leaks.
    # NB: the MT5 local cache has no reliable H4/H1 before ~2021-07, and
    # bias is NEUTRAL until the first structure break, so switching this
    # on effectively truncates the run to the LTF-covered window. That is
    # surfaced as ltf_data_start rather than silently shrinking the test.
    n_skipped_ltf_bias = 0
    ltf_data_start = None
    if req.use_ltf_bias:
        ltf_tf = mt5.TIMEFRAME_H4 if req.ltf_bias_timeframe == "H4" else mt5.TIMEFRAME_H1
        try:
            ltf = fvg_mod.fetch(symbol, ltf_tf, start, end)
        except RuntimeError as e:
            raise HTTPException(404, f"{req.ltf_bias_timeframe} data unavailable: {e}")
        # Older history comes back as one degenerate bar per day, so
        # "H4 structure" there would really be daily structure computed on
        # fake bars. Cut the run to where the feed is genuinely intraday
        # instead of filtering on meaningless structure.
        reliable_from = reliable_intraday_start(ltf)
        if reliable_from is None:
            raise HTTPException(
                404,
                f"No usable intraday {req.ltf_bias_timeframe} history for {symbol}. "
                f"Open its {req.ltf_bias_timeframe} chart in MT5 and scroll back to download more.",
            )
        ltf = ltf[ltf["time"] >= reliable_from].reset_index(drop=True)
        structure = compute_structure(ltf, size=req.ltf_bias_size)
        ltf_times = ltf["time"].values
        ltf_data_start = int(pd.Timestamp(reliable_from).timestamp())
        m15_times = m15["time"].values
        cutoff = np.datetime64(pd.Timestamp(reliable_from).tz_localize(None))
        kept = [(idx, gap) for idx, gap in entries
                if m15_times[idx] >= cutoff
                and bias_at(structure, ltf_times, m15_times[idx]) == SMC_BULLISH]
        n_skipped_ltf_bias = len(entries) - len(kept)
        entries = kept

    if req.use_magnet_leg:
        leg1_entries = find_leg1_short_entries(d1, m15)
        for _, gap in leg1_entries:
            gap["_atr"] = atr[gap["formed_at"]] if not np.isnan(atr[gap["formed_at"]]) else 0.0
        trades_df, curve = simulate_bidirectional(m15, leg1_entries, entries, leg1_risk_mult=req.magnet_leg_risk_mult)
        entries = entries + leg1_entries  # for gap lookup below
    else:
        trades_df, curve = fvg_mod.simulate(m15, entries)
    if not trades_df.empty and "direction" not in trades_df.columns:
        trades_df["direction"] = "long"

    decimals = 2 if symbol in ("USTEC", "XAUUSD") else (3 if symbol == "USDJPY" else 5)
    candles = [
        {"time": int(t.timestamp()), "open": round(float(o), decimals), "high": round(float(h), decimals),
         "low": round(float(l), decimals), "close": round(float(c), decimals)}
        for t, o, h, l, c in zip(d1["time"], d1["open"], d1["high"], d1["low"], d1["close"])
    ]
    candles_m15 = [
        {"time": int(t.timestamp()), "open": round(float(o), decimals), "high": round(float(h), decimals),
         "low": round(float(l), decimals), "close": round(float(c), decimals)}
        for t, o, h, l, c in zip(m15["time"], m15["open"], m15["high"], m15["low"], m15["close"])
    ]

    if trades_df.empty:
        return _clean({"symbol": symbol, "candles": candles, "candles_m15": candles_m15,
                        "metrics": None, "trades": []})

    n = len(trades_df)
    wins = int((trades_df["pnl"] > 0).sum())
    profit = float(trades_df.loc[trades_df["pnl"] > 0, "pnl"].sum())
    loss = float(-trades_df.loc[trades_df["pnl"] < 0, "pnl"].sum())
    vals = np.asarray(curve)
    peak = np.maximum.accumulate(vals)
    metrics = {
        "trades": n, "win_rate": 100 * wins / n, "avg_r": float(trades_df["r_multiple"].mean()),
        "profit_factor": (profit / loss) if loss else None,
        "max_drawdown_pct": float(((vals - peak) / peak).min() * 100),
        "final_equity": float(curve[-1]), "start_equity": fvg_mod.START_EQUITY,
        "total_return_pct": 100 * (curve[-1] / fvg_mod.START_EQUITY - 1),
        "equity_curve": [float(x) for x in curve],
        "target_rr": req.target_rr,
        "atr_buffer_mult": req.atr_buffer_mult,
        "entry_style": req.entry_style,
        "use_resistance_filter": req.use_resistance_filter,
        "n_skipped_resistance": n_skipped_resistance,
        "use_magnet_leg": req.use_magnet_leg,
        "magnet_leg_risk_mult": req.magnet_leg_risk_mult,
        "risk_pct": req.risk_pct,
        "sizing_mode": req.sizing_mode,
        "fixed_lots": req.fixed_lots,
        "contract_size": contract_size,
        "volume_min": symbol_info.volume_min,
        "use_ltf_bias": req.use_ltf_bias,
        "ltf_bias_timeframe": req.ltf_bias_timeframe,
        "ltf_bias_size": req.ltf_bias_size,
        "n_skipped_ltf_bias": n_skipped_ltf_bias,
        "ltf_data_start": ltf_data_start,
    }

    # match trades back to their originating gap by M15 entry index
    entries_by_idx = dict(entries)
    trade_out = []
    for _, tr in trades_df.iterrows():
        gap = entries_by_idx.get(tr["entry_idx"], {})
        entered_day_idx = gap.get("_entered_day_idx")
        entered_time = d1["time"].iloc[entered_day_idx] if entered_day_idx is not None else None
        formed_time = d1["time"].iloc[gap["formed_at"]] if "formed_at" in gap else None
        direction = tr.get("direction", "long")

        if direction == "short":
            narrative = (
                (f"A bullish Fair Value Gap formed on {formed_time.strftime('%Y-%m-%d')} "
                 f"({gap['bottom']:.{decimals}f}-{gap['top']:.{decimals}f}), still unfilled. ")
                if formed_time is not None else ""
            ) + (
                f"Price came back down and tagged the gap's top edge on "
                f"{pd.Timestamp(tr['entry_time']).strftime('%Y-%m-%d')} -- shorted there at "
                f"{tr['entry_price']:.{decimals}f}, riding the fill toward the gap's bottom (the magnet). "
                f"Stop at {tr['stop']:.{decimals}f} (entry plus {req.atr_buffer_mult:.2f}x ATR buffer). "
                f"Target at {tr['target']:.{decimals}f} (full gap fill). "
                f"Exited via {tr['exit_reason']} for {tr['r_multiple']:+.2f}R (${tr['pnl']:,.2f})."
            )
        else:
            narrative = (
                (f"A bullish Fair Value Gap formed on {formed_time.strftime('%Y-%m-%d')} "
                 f"({gap['bottom']:.{decimals}f}-{gap['top']:.{decimals}f}). ")
                if formed_time is not None else ""
            ) + (
                (f"Price retraced back into the gap on {entered_time.strftime('%Y-%m-%d')}. "
                 f"Bought at the next day's open, {tr['entry_price']:.{decimals}f}. ")
                if req.entry_style == "retracement"
                else (f"Bought at the open the day after it confirmed, {tr['entry_price']:.{decimals}f} "
                      f"(continuation entry -- no retracement wait). ")
            ) + (
                f"Stop at {tr['stop']:.{decimals}f} (gap bottom minus {req.atr_buffer_mult:.2f}x ATR buffer). "
                f"Target at {tr['target']:.{decimals}f} ({req.target_rr:.1f}:1 reward:risk). "
                f"Exited via {tr['exit_reason']} for {tr['r_multiple']:+.2f}R (${tr['pnl']:,.2f})."
            )

        trade_out.append({
            "direction": direction,
            "entry_time": int(pd.Timestamp(tr["entry_time"]).timestamp()),
            "entry_price": float(tr["entry_price"]), "stop": float(tr["stop"]), "target": float(tr["target"]),
            "exit_time": int(pd.Timestamp(tr["exit_time"]).timestamp()), "exit_price": float(tr["exit_price"]),
            "exit_reason": tr["exit_reason"], "pnl": float(tr["pnl"]), "r_multiple": float(tr["r_multiple"]),
            "gap_top": float(gap.get("top")) if gap.get("top") is not None else None,
            "gap_bottom": float(gap.get("bottom")) if gap.get("bottom") is not None else None,
            "gap_formed_time": int(formed_time.timestamp()) if formed_time is not None else None,
            "entered_time": int(entered_time.timestamp()) if entered_time is not None else None,
            "why": narrative,
        })

    return _clean({
        "symbol": symbol, "candles": candles, "candles_m15": candles_m15,
        "metrics": metrics, "trades": trade_out,
    })


@app.get("/api/candles")
def candles(symbol: str, timeframe: str, start: str, end: str):
    if timeframe not in TIMEFRAMES:
        raise HTTPException(400, f"timeframe must be one of {list(TIMEFRAMES.keys())}")
    if not mt5.initialize():
        raise HTTPException(500, f"MT5 initialize failed: {mt5.last_error()}")
    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)
    rates = mt5.copy_rates_range(symbol, TIMEFRAMES[timeframe], start_dt, end_dt)
    if rates is None or len(rates) == 0:
        raise HTTPException(404, f"No data for {symbol} {timeframe} in that range")
    df = pd.DataFrame(rates)
    # Replay displays completed candles only.
    now = pd.Timestamp.now(tz="UTC").timestamp()
    df = df.loc[df["time"] + TF_MINUTES[timeframe] * 60 <= now]
    out = [
        {"time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
         "low": float(r["low"]), "close": float(r["close"])}
        for _, r in df.iterrows()
    ]
    return {"candles": out}


@app.get("/api/health")
def health():
    return {"ok": True}
