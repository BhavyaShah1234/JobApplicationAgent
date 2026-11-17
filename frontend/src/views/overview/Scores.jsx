import Histogram from "../../charts/Histogram.jsx";
import DataTable from "../../components/DataTable.jsx";
import Panel from "../../components/Panel.jsx";

export default function ScoresSection({ a }) {
  return (
    <>
      <h2>Resume scores</h2>
      <div className="grid2">
        <Panel title="Average checker score per job" note={`${a.ats.jobs} current resume/JD pairs have every required checker score. ${a.ats.incomplete || 0} packets are incomplete or unverifiable and excluded from this average. Sites are not comparable to each other.`}>
          <Histogram label="ATS average" bins={a.ats.hist} />
        </Panel>
        <Panel title="By checker site">
          <DataTable cols={["Site", "Mean", "Jobs"]} rows={a.ats.by_site} rowKey={(s) => s.site} render={(s) => [s.site, s.mean, s.n]} />
        </Panel>
      </div>
    </>
  );
}
