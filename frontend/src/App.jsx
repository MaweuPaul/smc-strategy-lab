import { useState } from "react";
import AsianPanel from "./AsianPanel";
import DailyObPanel from "./DailyObPanel";
import DailyFvgPanel from "./DailyFvgPanel";
import OrbNyOpenPanel from "./OrbNyOpenPanel";
import "./App.css";

// The HTF/LTF Sweep and original session-open ORB strategies, and P404
// Sweep, were removed entirely (endpoint, panel, and strategy-specific
// code deleted) for showing no edge worth keeping in the dashboard.
// htf_ltf_backtest.py and p404_sweep_reversal.py remain on disk as shared
// infrastructure only (resolve_target/simulate_exit/summarize and
// compute_atr14 respectively, imported by the strategies below) -- see the
// top-level README.md for what was removed and why.
//
// The NY-open ORB tab uses a DIFFERENT rule from the removed session-open
// ORB (breakout of the candle immediately before 9:30am New York, not the
// first N minutes after a fixed UTC session open) -- see orb_ny_open/.
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
