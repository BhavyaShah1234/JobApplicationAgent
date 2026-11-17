import { usePoll } from "../hooks.js";
import Chip from "../components/Chip.jsx";
import DataTable from "../components/DataTable.jsx";
import BarList from "../charts/BarList.jsx";
import FunnelChart from "../charts/FunnelChart.jsx";
import Histogram from "../charts/Histogram.jsx";
import IntervalDot from "../charts/IntervalDot.jsx";
import TimelineChart from "../charts/TimelineChart.jsx";

const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const hours = (h) => (h == null ? "–" : h < 48 ? `${Math.round(h * 10) / 10} h` : `${Math.round(h / 24)} d`);

function Stat({ n, label, sub }) {
  return (
    <div className="card">
      <b>{n}</b>
      <span>{label}</span>
      {sub && <div className="mut" style={{ fontSize: 12 }}>{sub}</div>}
    </div>
  );
}

function Panel({ title, note, children }) {
  return (
    <div className="box">
      <h3 style={{ margin: "0 0 10px", fontSize: 15 }}>{title}</h3>
      {children}
      {note && <p className="mut" style={{ fontSize: 12, margin: "10px 0 0" }}>{note}</p>}
    </div>
  );
}

function GroupTable({ title, groups, min }) {
  if (!groups.length) return null;
  return (
    <Panel title={title} note={`A group needs ${min} resolved applications (replied, rejected, or silent past the cutoff) before its rate means anything; smaller ones are greyed.`}>
      <DataTable
        cols={["Group", "Applications", "Resolved", "Replied", "Share that replied (95% interval)", "Median days to reply"]}
        rows={groups}
        rowKey={(g) => g.key}
        render={(g) => [g.key, g.n, g.resolved, `${g.positive + g.negative}`, <IntervalDot key="i" rate={g.response_rate} ci={g.response_ci} tooFew={g.too_few} />, g.median_days != null ? g.median_days.toFixed(1) : "–"]}
      />
    </Panel>
  );
}

export default function Analytics() {
  const { data: a } = usePoll("/api/analytics", 20000);
  if (!a) return <p className="mut">Loading…</p>;
  if (!a.funnel.length) return <div className="empty">No data yet: analytics appear once postings have been discovered.</div>;
  const o = a.outcomes;
  const submitted = a.totals.submitted;
  return (
    <div>
      {submitted < o.min_group && (
        <div className="notice">
          Only {submitted} application{submitted === 1 ? "" : "s"} submitted so far. Response rates need at least {o.min_group} resolved applications per group, so the outcome
          figures below describe what happened, not what to expect. The discovery numbers are solid.
        </div>
      )}
      <div className="cards">
        <Stat n={a.totals.jobs.toLocaleString()} label="postings found" sub={`${a.funnel[1].n.toLocaleString()} passed the filters`} />
        <Stat n={a.totals.applications} label="applications filled to review" />
        <Stat n={submitted} label="submitted" sub={a.funnel[4] ? `${a.funnel[4].n} receipt confirmed` : ""} />
        <Stat n={o.rate ? pct(o.rate.k / o.rate.n) : "–"} label="of resolved applications got a reply" sub={o.rate ? `${o.rate.k} of ${o.rate.n}; 95% interval ${pct(o.rate.lo)}–${pct(o.rate.hi)}` : "none resolved yet"} />
        <Stat n={hours(a.latency.fill_to_submit_h)} label="median wait for your approval" sub={a.latency.n_submit ? `${a.latency.n_submit} applications` : ""} />
      </div>

      <h2>From posting to answer</h2>
      <Panel title="Funnel" note="Each bar is a count; the percentage is the share kept from the step above.">
        <FunnelChart steps={a.funnel} />
      </Panel>

      <h2>Activity, last {a.timeline.length} days</h2>
      <div className="grid2">
        <Panel title="Your applications" note="Filled to review, submitted, and replies received, per day.">
          <TimelineChart days={a.timeline} show={["filled", "submitted", "responses"]} />
        </Panel>
        <Panel title="Postings found" note="What discovery brought in each day, before any filtering.">
          <TimelineChart days={a.timeline} show={["found"]} />
        </Panel>
      </div>

      <h2>Discovery</h2>
      <div className="grid2">
        <Panel title="Why postings were filtered out" note="Most postings are not for you: wrong place or no matching role.">
          <BarList label="Skip reasons" items={a.skips.map((s) => ({ label: s.reason, n: s.n }))} />
          {a.skip_places.length > 0 && (
            <>
              <h4 style={{ margin: "12px 0 6px", fontSize: 13 }} className="mut">Top places outside the US</h4>
              <BarList label="Places" items={a.skip_places.map((s) => ({ label: s.place, n: s.n }))} />
            </>
          )}
        </Panel>
        <Panel title="Postings by status">
          <BarList label="Status" items={Object.entries(a.jobs_by_status).map(([k, v]) => ({ label: k, n: v })).sort((x, y) => y.n - x.n)} />
        </Panel>
        <Panel title="Companies with the most postings" note="Found, and how many of those passed the filters.">
          <DataTable cols={["Company", "Found", "Passed"]} rows={a.jobs_by_company} rowKey={(c) => c.company} render={(c) => [c.company, c.found.toLocaleString(), c.kept.toLocaleString()]} />
        </Panel>
        <Panel title="Role match of the kept postings">
          <BarList label="Roles" items={a.jobs_by_role.map((r) => ({ label: r.role, n: r.n }))} />
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }} className="mut">Match score</h4>
          <Histogram label="Match score" bins={a.score_hist} />
        </Panel>
      </div>

      <h2>Outcomes</h2>
      <div className="grid2">
        <Panel title="What came back" note="Silence is not counted as a rejection: an application is pending until the cutoff passes, then ghosted (a weak negative).">
          {Object.keys(o.labels).length ? (
            <BarList label="Outcome labels" items={Object.entries(o.labels).map(([k, v]) => ({ label: k, n: v }))} />
          ) : (
            <p className="mut">No submitted application yet.</p>
          )}
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
          <div className="cards">
            <Stat n={o.median_days != null ? `${o.median_days.toFixed(1)} d` : "–"} label="median days to first reply" sub={o.median_days == null ? "needs more replies" : "Kaplan–Meier"} />
            <Stat n={hours(a.latency.discovery_to_fill_h)} label="median time from posting to a filled form" />
          </div>
        </Panel>
      </div>
      <GroupTable title="By portal" groups={o.by_portal} min={o.min_group} />
      <GroupTable title="By role" groups={o.by_role} min={o.min_group} />
      <GroupTable title="By resume score band" groups={o.by_ats} min={o.min_group} />

      <h2>Resume scores</h2>
      <div className="grid2">
        <Panel title="Average checker score per job" note={`${a.ats.jobs} jobs scored. Sites are not comparable to each other, so the average is per job, from each site's last round.`}>
          <Histogram label="ATS average" bins={a.ats.hist} />
        </Panel>
        <Panel title="By checker site">
          <DataTable cols={["Site", "Mean", "Jobs"]} rows={a.ats.by_site} rowKey={(s) => s.site} render={(s) => [s.site, s.mean, s.n]} />
        </Panel>
      </div>
    </div>
  );
}
