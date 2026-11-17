import BarList from "../../charts/BarList.jsx";
import Histogram from "../../charts/Histogram.jsx";
import StackedBar from "../../charts/StackedBar.jsx";
import TwoToneBars from "../../charts/TwoToneBars.jsx";
import Panel from "../../components/Panel.jsx";

const KEPT = { queued: "Waiting in the queue", needs_review: "Needs your review", scraped: "Read and ready", applied: "Applied", closed: "Closed since" };

// Where every posting ended up: one bar for the whole, then three equal panels of the same height and the same number of rows.
export function discoverySegments(a) {
  const filtered = a.skips.map((s) => ({ label: s.reason, n: s.n }));
  const kept = Object.entries(a.jobs_by_status).filter(([k]) => k !== "skipped");
  const named = kept.filter(([k]) => KEPT[k]).map(([k, n]) => ({ label: KEPT[k], n }));
  const other = kept.filter(([k]) => !KEPT[k]).reduce((s, [, n]) => s + n, 0);
  return [...filtered, ...named, ...(other ? [{ label: "Other", n: other }] : [])];
}

export default function DiscoverySection({ a }) {
  const total = a.totals.jobs;
  return (
    <>
      <h2>Discovery</h2>
      <Panel title={`Where the ${total.toLocaleString()} postings went`} note="Most postings are not for you: the wrong place, or no target role fits the title.">
        <StackedBar label="Where postings went" segments={discoverySegments(a)} />
      </Panel>
      <div className="grid4" style={{ marginTop: 12 }}>
        <Panel title="Outside the US: top places" note="Postings dropped because the location is not in the United States.">
          <BarList label="Places" items={a.skip_places.slice(0, 6).map((s) => ({ label: s.place, n: s.n }))} />
        </Panel>
        <Panel title="Companies with the most postings" note="Pale: found. Strong: passed the filters.">
          <TwoToneBars label="Companies" items={a.jobs_by_company.slice(0, 6).map((c) => ({ label: c.company, n: c.found, part: c.kept }))} />
        </Panel>
        <Panel title="Roles of the kept postings" note="The target role each kept posting was matched to.">
          <BarList label="Roles" items={a.jobs_by_role.slice(0, 6).map((r) => ({ label: r.role === "(none)" ? "No role matched" : r.role, n: r.n }))} />
        </Panel>
        <Panel title="Title match score" note="How well the kept postings' titles fit your target roles (1.0 is a certain match).">
          <Histogram label="Match score" bins={a.score_hist} />
        </Panel>
      </div>
    </>
  );
}
