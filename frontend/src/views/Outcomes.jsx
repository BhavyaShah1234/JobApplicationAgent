import { ago } from "../format.js";
import { usePoll } from "../hooks.js";
import Chip from "../components/Chip.jsx";
import DataTable from "../components/DataTable.jsx";
import FetchError from "../components/FetchError.jsx";

export default function Outcomes() {
  const { data, error, reload } = usePoll("/api/outcomes", 15000);
  if (!data) return <><FetchError error={error} reload={reload} /><p className="mut">Loading…</p></>;
  const entries = Object.entries(data.counts);
  return (
    <div>
      <FetchError error={error} reload={reload} />
      <div className="box">
        {entries.length
          ? entries.map(([k, v]) => (
              <span key={k} style={{ marginRight: 12 }}>
                <Chip>{k}</Chip> {v}
              </span>
            ))
          : "No outcomes recorded yet."}
      </div>
      <DataTable
        cols={["Received", "Stage", "Company", "Title", "Subject"]}
        rows={data.recent}
        render={(x) => [ago(x.received_at), <Chip key="s">{x.stage}</Chip>, x.company, x.title, <span key="j" className="mut">{x.subject || ""}</span>]}
      />
    </div>
  );
}
