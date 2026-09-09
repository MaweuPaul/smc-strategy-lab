import { useState } from "react";
import AsianPanel from "./AsianPanel";
import DailyObPanel from "./DailyObPanel";
import DailyFvgPanel from "./DailyFvgPanel";
import OrbNyOpenPanel from "./OrbNyOpenPanel";
import "./App.css";

// HTF/LTF and the original session-open ORB tabs were removed: both showed
// much weaker edges than these two (HTF/LTF ~0.11-0.18 avg R after bug
// fixes; the session-open ORB's best config only +0.03 avg R) -- not worth
// keeping in the dashboard. Their code is still on disk (HtfLtfPanel.jsx,
// OrbPanel.jsx, htf_ltf_backtest.py, orb_backtest.py, and the matching
// backend endpoints) if we want them back. The NY-open ORB below is a
// DIFFERENT rule (breakout of the candle immediately before 9:30am New
// York, not the first N minutes after a fixed UTC session open) added
// later and kept -- see orb_backtest.py's run_orb_ny_open().
//
// P404 Sweep was removed for the same reason: negative avg R on all 6
// tested symbols (USTEC/XAUUSD/EURUSD/GBPUSD/USDJPY/US30) on BOTH its
// engines (the M3-precision MSS entry wired to the old endpoint, and its
// own M15-only simulate()), at both 12mo and 36mo windows -- no config
// found that made it profitable anywhere. p404_sweep_reversal.py stays on
// disk because daily_fvg_newday.py and others import its compute_atr14 /
// _server_utc_offset_hours helpers; p404_sweep_mss.py (the MSS-only entry
// logic) and the frontend panel were deleted since nothing else uses them.
const TABS = [
  { key: "asian", label: "Asian Session", Component: AsianPanel },
  { key: "dailyob", label: "Daily OB", Component: DailyObPanel },
  { key: "dailyfvg", label: "Daily FVG", Component: DailyFvgPanel },
  { key: "orbny", label: "ORB (NY Open)", Component: OrbNyOpenPanel },
];

export default function App() {
  const [tab, setTab] = useState("asian");
  const Active = TABS.find((t) => t.key === tab).Component;

  return (
    <div>
      <div className="tab-bar">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={"tab" + (tab === t.key ? " tab-active" : "")}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <Active />
    </div>
  );
}
