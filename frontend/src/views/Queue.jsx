import { useState } from "react";
import { ago } from "../format.js";
import { usePoll } from "../hooks.js";
import Chip from "../components/Chip.jsx";
import DataTable from "../components/DataTable.jsx";
import FetchError from "../components/FetchError.jsx";

export default function Queue() {
  const [q, setQ] = useState("");
  const { data, error, reload } = usePoll("/api/queue", 10000);
  if (!data) return <><FetchError error={error} reload={reload} /><p className="mut">Loading…</p></>;
  const needle = q.toLowerCase();
  const rows = data.filter((x) => !needle || `${x.company} ${x.title} ${x.status} ${x.role_id} ${x.notes}`.toLowerCase().includes(needle));
  return (
    <div>
      <FetchError error={error} reload={reload} />
      <div className="tools">
        <input type="search" placeholder="Filter by company, title or status" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="mut">{rows.length} of {data.length} postings</span>
      </div>
      <DataTable
        cols={["Company", "Title", "Status", "Role", "Score", "Updated", "Note"]}
        rows={rows}
        rowKey={(x) => x.id}
        render={(x) => [x.company, x.title, <Chip key="s">{x.status}</Chip>, x.role_id || "", x.match_score ?? "", ago(x.updated_at), <span key="n" className="mut">{x.notes || ""}</span>]}
      />
    </div>
  );
}
