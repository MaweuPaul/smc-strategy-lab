from pathlib import Path
import shutil
root = Path(__file__).resolve().parent
backup = root / 'audit_backups' / 'daily_ob_20260907'

def edit(name, transform):
    path = root / name
    saved = backup / name
    if not saved.exists():
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, saved)
    path.write_text(transform(path.read_text(encoding='utf-8')), encoding='utf-8')

def api(s):
    s = s.replace('from typing import List, Optional', 'from typing import List, Optional, Literal\nfrom uuid import uuid4')
    s = s.replace('from pydantic import BaseModel', 'from pydantic import BaseModel, Field, model_validator')
    s = s.replace('from daily_ob_backtest import run_daily_ob', 'from daily_ob_backtest import run_daily_ob\nfrom daily_ob_support import account_trades, TF_MINUTES')
    a, b = s.index('class DailyObBacktestRequest'), s.index('\n\ndef compute_cagr')
    s = s[:a] + '''class DailyObBacktestRequest(BaseModel):
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
''' + s[b:]
    a, b = s.index('def compute_cagr'), s.index('\n\ndef _clean')
    s = s[:a] + '''def compute_cagr(combined: pd.DataFrame, final_equity: float, start_equity: float,
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
''' + s[b:]
    a, b = s.index('def daily_ob_backtest('), s.index('\n\n@app.get("/api/candles")')
    block = s[a:b]
    block = block.replace('    errors = {}', '''    errors = {}
    coverage = {}
    as_of = pd.Timestamp.now(tz="UTC")
    metadata = {"run_id": str(uuid4()), "engine_version": "daily-ob-causal-20260907",
                "config": req.model_dump(), "as_of": as_of.isoformat(), "ltf": ltf_name,
                "execution_model": "next-open entries/invalidation; gap fills; stop-first OHLC",
                "cost_model": "gross prices: excludes spread, commission, slippage and financing"}''')
    block = block.replace('monitor_days=monitor_days, max_hold_bars=max_hold_bars)', 'monitor_days=monitor_days, max_hold_bars=max_hold_bars, as_of=as_of)\n            if trades.attrs.get("evaluation_start") is not None:\n                coverage[sym] = dict(trades.attrs)')
    block = block.replace('return {"trades": [], "metrics": None, "per_symbol": {}, "errors": errors, "ltf": "H1"}', 'return _clean({**metadata, "trades": [], "metrics": None, "per_symbol": {}, "errors": errors, "coverage": coverage})')
    c, d = block.index('    combined = pd.concat'), block.index('    per_symbol = {}')
    block = block[:c] + '''    combined, ledger = account_trades(pd.concat(all_trades, ignore_index=True), req.risk_pct, req.start_equity)
    evaluation_start = min(c["evaluation_start"] for c in coverage.values())
    evaluation_end = max(c["evaluation_end"] for c in coverage.values())
    cagr_pct, years_span = compute_cagr(combined, ledger["final_equity"], req.start_equity,
                                      evaluation_start, evaluation_end)
    metrics = {**ledger, "trades": len(combined),
               "win_rate": float((combined.r_multiple > 0).mean() * 100),
               "avg_r": float(combined.r_multiple.mean()),
               "cagr_pct": cagr_pct, "years_span": years_span,
               "evaluation_start": evaluation_start, "evaluation_end": evaluation_end}

''' + block[d:]
    block = block.replace('    return {\n        "trades": trades_records,', '    return _clean({\n        **metadata, "coverage": coverage,\n        "trades": trades_records,')
    block = block.replace('        "ltf": "H1",\n    }', '        "ltf": ltf_name,\n    })')
    s = s[:a] + block + s[b:]
    s = s.replace('    df = pd.DataFrame(rates)\n    out = [', '''    df = pd.DataFrame(rates)
    # Replay displays completed candles only.
    now = pd.Timestamp.now(tz="UTC").timestamp()
    df = df.loc[df["time"] + TF_MINUTES[timeframe] * 60 <= now]
    out = [''')
    return s

edit('backend_api.py', api)
print('Daily OB API accounting, metadata and validation updated.')
