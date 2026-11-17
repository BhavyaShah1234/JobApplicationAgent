import Histogram from "../../charts/Histogram.jsx";
import DataTable from "../../components/DataTable.jsx";
import Panel from "../../components/Panel.jsx";

export default function ScoresSection({ a }) {
  return (
    <>
      <h2>Resume scores</h2>
      <div className="grid2">
        <Panel title="Average checker score per job" note={`${a.ats.jobs} jobs scored. Sites are not comparable to each other, so the average is per job, from each site's last round.`}>
          <Histogram label="ATS average" bins={a.ats.hist} />
        </Panel>
        <Panel title="By checker site">
          <DataTable cols={["Site", "Mean", "Jobs"]} rows={a.ats.by_site} rowKey={(s) => s.site} render={(s) => [s.site, s.mean, s.n]} />
        </Panel>
      </div>
    </>
  );
}
