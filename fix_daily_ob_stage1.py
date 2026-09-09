from pathlib import Path
import shutil

root = Path(__file__).resolve().parent
backup = root / 'audit_backups' / 'daily_ob_20260907'
backup.mkdir(parents=True, exist_ok=True)

def edit(name, transform):
    path = root / name
    saved = backup / name
    if not saved.exists():
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, saved)
    text = path.read_text(encoding='utf-8')
    path.write_text(transform(text), encoding='utf-8')

def engine(s):
    s = s.replace('    HTF_TO_LTF, DEFAULT_KILLZONES, fetch_htf_ltf, in_killzone,\n    resolve_target, simulate_exit, summarize,', '    DEFAULT_KILLZONES, fetch_htf_ltf, in_killzone,')
    s = s.replace('\n\n\ndef fetch_ltf_custom', '\nfrom daily_ob_support import completed_bars, simulate_daily_exit, account_trades\n\n\ndef fetch_ltf_custom', 1)
    s = s.replace('range(entry_idx, base_exit_idx + 1)', 'range(entry_idx, base_exit_idx)')
    s = s.replace('First bar in [entry_idx, base_exit_idx]', 'First bar in [entry_idx, base_exit_idx)')
    s = s.replace('    start = max(0, entry_idx - lookback)', '    if minor_window < 1 or confirm_bars < 1 or grace_bars < 0 or lookback < 1:\n        raise ValueError("Invalid invalidation parameters")\n    start = max(0, entry_idx - lookback)')
    s = s.replace('+ minor_window <= local_entry', '+ minor_window < local_entry')
    s = s.replace('htf["time"] <= t', 'htf["close_time"] <= t')
    a, b = s.index('def filter_obs_by_displacement'), s.index('\ndef run_daily_ob')
    s = s[:a] + '''def filter_obs_by_displacement(obs, htf, fvg_window=3):
    """Keep qualifying OBs, timestamped when displacement first becomes known."""
    fvgs = find_fvgs(htf)
    kept = []
    for ob in obs:
        matches = [g for g in fvgs if g["type"] == ob["type"]
                   and abs(g["formed_at"] - ob["formed_at"]) <= fvg_window]
        if matches:
            gap = min(matches, key=lambda g: g["formed_at"])
            kept.append({**ob, "qualified_at": max(ob["formed_at"], gap["formed_at"]),
                         "fvg_formed_at": gap["formed_at"],
                         "fvg_top": gap["top"], "fvg_bottom": gap["bottom"]})
    return kept

''' + s[b:]
    s = s.replace('breakeven_trigger_r=None, breakeven_buffer_r=0.0):', 'breakeven_trigger_r=None, breakeven_buffer_r=0.0,\n                  *, htf_data=None, ltf_data=None, as_of=None):')
    a, b = s.index('    entry_ltf_name ='), s.index('    swing_highs, swing_lows = find_swings(htf')
    s = s[:a] + '''    if (swing_window < 1 or ob_lookback < 1 or max_hold_bars < 1 or monitor_days < 1
            or choch_minor_window < 1 or invalidation_minor_window < 1
            or invalidation_confirm_bars < 1 or invalidation_grace_bars < 0
            or invalidation_lookback < 1 or displacement_fvg_window < 0
            or zone_buffer_frac < 0 or not 0 < min_rr <= max_rr or pyramid_risk_mult <= 0):
        raise ValueError("Invalid strategy windows, risk or RR bounds")
    if bias_mode not in ("off", "filter", "reverse") or entry_precision not in ("h1", "m15_choch"):
        raise ValueError("Unsupported bias or entry mode")
    if pyramid_trigger_r is not None and pyramid_trigger_r <= 0:
        raise ValueError("Pyramid trigger must be positive")
    entry_ltf_name = "M15" if entry_precision == "m15_choch" else ltf_name
    if (htf_data is None) != (ltf_data is None):
        raise ValueError("Provide both HTF and LTF data, or neither")
    htf, ltf = fetch_htf_ltf(symbol, htf_name, ltf_name, htf_bars) if htf_data is None else (htf_data, ltf_data)
    if entry_precision == "m15_choch" and ltf_data is None:
        ltf = fetch_ltf_custom(symbol, "M15", htf.time.iloc[0] - timedelta(days=2),
                               htf.time.iloc[-1] + timedelta(days=2))
    cutoff = pd.Timestamp.now(tz="UTC") if as_of is None else as_of
    htf = completed_bars(htf, htf_name, cutoff)
    ltf = completed_bars(ltf, entry_ltf_name, cutoff)
    if htf.empty or ltf.empty:
        return pd.DataFrame()
    coverage = {"evaluation_start": max(htf.time.iloc[0], ltf.time.iloc[0]),
                "evaluation_end": ltf.close_time.iloc[-1], "ltf": entry_ltf_name}
    print(f"{htf_name}: {len(htf)} closed bars, {entry_ltf_name}: {len(ltf)} closed bars")

''' + s[b:]
    s = s.replace('    obs.sort(key=lambda o: o["formed_at"])', '''    for ob in obs:
        ob["confirmed_time"] = htf.close_time.iloc[ob["formed_at"]]
        ob["available_time"] = htf.close_time.iloc[ob.get("qualified_at", ob["formed_at"])]
    obs.sort(key=lambda o: o["formed_at"])''')
    s = s.replace('        if ob["formed_at"] + 1 >= len(htf):\n            continue\n', '')
    s = s.replace('confirmed_time = htf["time"][ob["formed_at"] + 1]', 'confirmed_time = ob["available_time"]')
    s = s.replace('window_end_time = htf["time"][window_end_htf_idx]', 'window_end_time = htf["close_time"][window_end_htf_idx]')
    s = s.replace('(ltf["time"] <= window_end_time)', '(ltf["close_time"] <= window_end_time)')
    s = s.replace('bias_at_time(htf, bias_arr, touch_time)', 'bias_at_time(htf, bias_arr, window.close_time.iloc[touch_idx])')
    s = s.replace('        if killzones is not None and not in_killzone(signal_time, killzones):\n            continue\n\n', '')
    s = s.replace('        entry_price = ltf["open"][entry_idx]', '        if killzones is not None and not in_killzone(ltf.time.iloc[entry_idx], killzones):\n            continue\n        entry_price = ltf["open"][entry_idx]')
    s = s.replace('o["formed_at"] <= ob["formed_at"]', 'o["available_time"] <= ltf.close_time.iloc[gi]')
    a, b = s.index('        outcome, exit_price, exit_idx, r = simulate_exit('), s.index('        candidates.append({')
    s = s[:a] + '''        invalidation_idx = None
        if use_invalidation_exit:
            invalidation_idx = find_invalidation_exit(
                ltf, entry_idx, min(entry_idx + max_hold_bars - 1, len(ltf) - 1), direction,
                minor_window=invalidation_minor_window, lookback=invalidation_lookback,
                confirm_bars=invalidation_confirm_bars, grace_bars=invalidation_grace_bars)
        fill = simulate_daily_exit(
            ltf, entry_idx, entry_price, stop, target_price, direction, max_hold_bars, risk,
            invalidation_idx, breakeven_trigger_r, breakeven_buffer_r)
        exit_idx = fill["exit_idx"]
        chain_id = f"{symbol}:{formed_time.isoformat()}:{ob['type']}"
        common = {
            "symbol": symbol, "chain_id": chain_id, "ob_type": ob["type"],
            "ob_top": ob["top"], "ob_bottom": ob["bottom"],
            "ob_formed_time": formed_time, "ob_confirmed_time": ob["confirmed_time"],
            "ob_available_time": ob["available_time"],
            "ob_candle_time": htf.time.iloc[ob["candle_idx"]],
            "fvg_confirmed_time": htf.close_time.iloc[ob["fvg_formed_at"]] if "fvg_formed_at" in ob else None,
            "fvg_top": ob.get("fvg_top"), "fvg_bottom": ob.get("fvg_bottom"),
            "direction": "long" if direction == "bullish" else "short",
            "touch_time": touch_time, "touch_confirmed_time": window.close_time.iloc[touch_idx],
            "signal_time": signal_time, "signal_confirmed_time": ltf.close_time.iloc[gi],
            "ltf": entry_ltf_name, "htf": htf_name,
            "replay_end_time": ltf.time.iloc[entry_idx] + timedelta(minutes=max_hold_bars *
                (15 if entry_ltf_name == "M15" else 60)),
        }
        def record_fill(fill_data):
            event = invalidation_idx
            return {**fill_data, "invalidation_signal_time":
                    ltf.close_time.iloc[event] if event is not None and event < fill_data["exit_idx"] else None}
        base_trade = {**common, **record_fill(fill), "entry_time": ltf.time.iloc[entry_idx],
                      "entry": entry_price, "stop": stop, "target": target_price,
                      "rr_planned": rr_target, "leg_type": "base", "risk_mult": 1.0}
        chain_end_time = fill["exit_time"]
        pyramid_trade = None
        if pyramid_trigger_r is not None:
            trig_idx = find_pyramid_trigger(ltf, entry_idx, exit_idx, entry_price, risk,
                                            direction, pyramid_trigger_r)
            if trig_idx is not None:
                add_entry_idx = trig_idx + 1
                # Cancel if the base exits at this open; an intrabar exit later
                # on the add's entry candle is not knowable at its open.
                alive = add_entry_idx < exit_idx or (add_entry_idx == exit_idx and fill["exit_phase"] != "open")
                permitted = killzones is None or in_killzone(ltf.time.iloc[add_entry_idx], killzones)
                if alive and permitted:
                    add_entry = ltf.open.iloc[add_entry_idx]
                    add_stop = entry_price
                    sign = 1 if direction == "bullish" else -1
                    add_risk = sign * (add_entry - add_stop)
                    if add_risk > 0 and sign * (target_price - add_entry) > 0:
                        add_rr = sign * (target_price - add_entry) / add_risk
                        # The base structure invalidation applies to the entire OB chain.
                        add_fill = simulate_daily_exit(ltf, add_entry_idx, add_entry, add_stop,
                            target_price, direction, max_hold_bars, add_risk, invalidation_idx)
                        pyramid_trade = {**common, **record_fill(add_fill),
                            "entry_time": ltf.time.iloc[add_entry_idx], "entry": add_entry,
                            "stop": add_stop, "target": target_price, "rr_planned": add_rr,
                            "signal_time": ltf.time.iloc[trig_idx],
                            "signal_confirmed_time": ltf.close_time.iloc[trig_idx],
                            "leg_type": "pyramid", "risk_mult": pyramid_risk_mult}
                        chain_end_time = max(chain_end_time, add_fill["exit_time"])

''' + s[b:]
    s = s.replace('    return pd.DataFrame(trades)\n', '    result = pd.DataFrame(trades)\n    result.attrs.update(coverage)\n    return result\n')
    s = s.replace('    summarize(trades, risk_pct=args.risk_pct)', '''    if not trades.empty:
        accounted, metrics = account_trades(trades, args.risk_pct, 10_000)
        print(f"Realized balance: {metrics['final_equity']:.2f}; realized drawdown: {metrics['max_drawdown_pct']:.2f}%")''')
    return s

edit('daily_ob_backtest.py', engine)
print('Daily OB timing and execution changes written; originals backed up.')
