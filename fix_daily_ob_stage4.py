from pathlib import Path
import shutil
root = Path(__file__).resolve().parent
path = root / 'frontend/src/TradeReplay.jsx'
backup = root / 'audit_backups/daily_ob_20260907/frontend/src/TradeReplay.jsx'
backup.parent.mkdir(parents=True, exist_ok=True)
if not backup.exists():
    shutil.copy2(path, backup)
s = path.read_text(encoding='utf-8')
a, b = s.index('function nearestIndex'), s.index('const TIMEFRAMES')
s = s[:a] + 'import { eventIndex, TF_SECONDS } from "./dailyObAnalysis";\n\n' + s[b:]
s = s.replace('TradeReplay({ trade, ltf })', 'TradeReplay({ trade, ltf, auditMode = true })')
s = s.replace('    setTimeframe(ltf);', '    setTimeframe(trade?.ltf || ltf);')
s = s.replace('  }, [trade?.id]);', '  }, [trade?.id, trade?.ltf, ltf]);')
s = s.replace('    setPlaying(false);\n    setError(null);', '    let active = true;\n    idxRef.current = {};\n    seriesRef.current?.setData([]);\n    setPlaying(false);\n    setError(null);')
s = s.replace('new Date(new Date(trade.exit_time).getTime() + pad)', 'new Date(new Date(trade.replay_end_time || new Date(Date.parse(trade.entry_time) + 30 * 86400000).toISOString()).getTime() + pad)')
s = s.replace('      .then((cs) => {\n        setCandles(cs);', '      .then((cs) => {\n        if (!active) return;\n        setCandles(cs);')
s = s.replace('nearestIndex(cs, trade.sweep_time)', 'eventIndex(cs, trade.touch_time || trade.sweep_time, timeframe)')
s = s.replace('nearestIndex(cs, trade.mss_time)', 'eventIndex(cs, trade.signal_confirmed_time || trade.mss_time, timeframe, !!trade.signal_confirmed_time)')
s = s.replace('nearestIndex(cs, trade.entry_time)', 'eventIndex(cs, trade.entry_time, timeframe)')
s = s.replace('nearestIndex(cs, trade.exit_time)', 'eventIndex(cs, trade.exit_time, timeframe, trade.exit_phase === "close")')
s = s.replace('.catch((e) => setError(e.response?.data?.detail || e.message))', '.catch((e) => { if (active) setError(e.response?.data?.detail || e.message); })')
s = s.replace('.finally(() => setLoading(false));', '.finally(() => { if (active) setLoading(false); });\n    return () => { active = false; };')
s = s.replace('  }, [trade]);', '  }, [trade, timeframe]);')
s = s.replace('    const currentIdx = revealed - 1;', '''    const currentIdx = revealed - 1;
    const clock = candles[currentIdx].time + TF_SECONDS[timeframe];
    const known = time => !!time && Date.parse(time) / 1000 <= clock;''')
s = s.replace('''    // HTF POI zone edges -- visible from the start
    ensureLine("poi_top", trade.poi_top, "#5b8def", "POI top");
    ensureLine("poi_bottom", trade.poi_bottom, "#5b8def", "POI bottom");''', '''    // Reveal an OB only when it was qualified, never at its source candle.
    if (known(trade.ob_available_time || trade.window_start_time)) {
      ensureLine("poi_top", trade.poi_top, "#5b8def", "Confirmed POI top");
      ensureLine("poi_bottom", trade.poi_bottom, "#5b8def", "Confirmed POI bottom");
    } else {
      ensureRemoved("poi_top"); ensureRemoved("poi_bottom");
    }
    if (known(trade.fvg_confirmed_time)) {
      ensureLine("fvg_top", trade.fvg_top, "#a080d0", "FVG top");
      ensureLine("fvg_bottom", trade.fvg_bottom, "#a080d0", "FVG bottom");
    } else {
      ensureRemoved("fvg_top"); ensureRemoved("fvg_bottom");
    }''')
s = s.replace('if (idx.entry != null && currentIdx >= idx.entry)', 'if (idx.entry != null && currentIdx >= idx.entry && known(trade.entry_time))')
s = s.replace('      ensureLine("stop", trade.stop, "#ef5350", "Stop");', '''      const changes = (trade.stop_changes || []).filter(change => known(change.time));
      const stop = changes.length ? changes.at(-1).stop : trade.stop;
      ensureRemoved("stop");
      ensureLine("stop", stop, "#ef5350", "Stop");''')
s = s.replace('if (idx.exit != null && currentIdx >= idx.exit)', 'if (idx.exit != null && currentIdx >= idx.exit && known(trade.exit_time))')
s = s.replace('text: "sweep (TS)"', 'text: trade.touch_time ? "OB touch" : "sweep (TS)"')
s = s.replace('text: trade.outcome === "win" ? "Exit (win)" : "Exit (loss)"', 'text: `Exit (${trade.exit_reason || trade.outcome})`')
s = s.replace('  }, [revealed, candles, trade]);', '  }, [revealed, candles, trade, timeframe]);')
s = s.replace('  if (!trade) return null;', '''  if (!trade) return null;
  const clock = candles[revealed - 1] ? candles[revealed - 1].time + TF_SECONDS[timeframe] : -Infinity;
  const showOutcome = !auditMode || clock >= Date.parse(trade.exit_time) / 1000;''')
s = s.replace('{trade.symbol} {timeframe} — {trade.direction} ({trade.outcome}, {trade.r_multiple?.toFixed(2)}R)', '{trade.symbol} {timeframe} — {trade.direction} {showOutcome ? `(${trade.outcome}, ${trade.r_multiple?.toFixed(2)}R)` : "(outcome hidden)"}')
s = s.replace('      <div className="replay-timeframes">', '''      <p className="hint">Completed-bar review: each step reveals a full candle through its close.
        Levels appear only once confirmed. Intrabar exit ordering is modeled, not reconstructed from ticks.</p>
      <div className="replay-timeframes">''')
s = s.replace('disabled={idxRef.current.exit == null}', 'disabled={idxRef.current.exit == null || (auditMode && !showOutcome)}')
s = s.replace('          Jump to sweep', '          Jump to touch/sweep')
path.write_text(s, encoding='utf-8')
print('Replay confirmation gates, event mapping and stale-response protection updated.')
