import { useState } from "react";
import BarList from "../../charts/BarList.jsx";
import IntervalDot from "../../charts/IntervalDot.jsx";
import Chip from "../../components/Chip.jsx";
import DataTable from "../../components/DataTable.jsx";
import Panel from "../../components/Panel.jsx";
import Stat from "../../components/Stat.jsx";
import { hours } from "./numbers.js";

const GROUPS = [["by_portal", "Portal"], ["by_role", "Role"], ["by_ats", "Resume score"]];

export default function OutcomesSection({ a }) {
  const o = a.outcomes;
  const [by, setBy] = useState("by_portal");
  const rows = o[by];
  return (
    <>
      <h2>Outcomes</h2>
      <div className="grid2">
        <Panel title="What came back" note="Silence is not counted as a rejection: an application is pending until the cutoff passes, then ghosted (a weak negative).">
          {Object.keys(o.labels).length ? <BarList label="Outcome labels" items={Object.entries(o.labels).map(([k, v]) => ({ label: k, n: v }))} /> : <p className="mut">No submitted application yet.</p>}
          {Object.keys(o.counts).length > 0 && (
            <div style={{ marginTop: 10 }}>
              {Object.entries(o.counts).map(([k, v]) => (
                <span key={k} style={{ marginRight: 12 }}>
                  <Chip>{k}</Chip> {v}
                </span>
              ))}
            </div>
          )}
        </Panel>
        <Panel title="Time to a reply" note={o.cutoff_basis ? `Ghosted after ${Math.round(o.cutoff_days)} days. ${o.cutoff_basis}` : ""}>
          <div className="cards two">
            <Stat n={o.median_days != null ? `${o.median_days.toFixed(1)} d` : "–"} label="median days to first reply" sub={o.median_days == null ? "needs more replies" : "Kaplan–Meier"} />
            <Stat n={hours(a.latency.discovery_to_fill_h)} label="posting to a filled form" sub="median" />
          </div>
        </Panel>
      </div>
      <div style={{ marginTop: 12 }}>
        <Panel title="Share that replied, by group" note={`A group needs ${o.min_group} resolved applications (replied, rejected, or silent past the cutoff) before its rate means anything; smaller ones are greyed.`}>
          <div className="tools">
            {GROUPS.map(([id, label]) => (
              <button key={id} type="button" className={`act sm${by === id ? "" : " sec"}`} onClick={() => setBy(id)}>
                {label}
              </button>
            ))}
          </div>
          {rows.length ? (
            <DataTable
              cols={["Group", "Applications", "Resolved", "Replied", "Share that replied (95% interval)", "Median days to reply"]}
              rows={rows}
              rowKey={(g) => g.key}
              render={(g) => [g.key, g.n, g.resolved, `${g.positive + g.negative}`, <IntervalDot key="i" name={g.key} k={g.positive + g.negative} n={g.resolved} rate={g.response_rate} ci={g.response_ci} tooFew={g.too_few} />, g.median_days != null ? g.median_days.toFixed(1) : "–"]}
            />
          ) : (
            <p className="mut">Nothing here yet.</p>
          )}
        </Panel>
      </div>
    </>
  );
}
