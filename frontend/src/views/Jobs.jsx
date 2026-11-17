import { useState } from "react";
import { usePoll } from "../hooks.js";
import Empty from "../components/Empty.jsx";
import JobCard from "../components/JobCard.jsx";

const FILTERS = [
  ["all", "All"],
  ["active", "Working now"],
  ["attention", "Needs attention"],
  ["done", "Finished"],
];

export default function Jobs({ overview, onWatch }) {
  const [f, setF] = useState("all");
  const { data } = usePoll("/api/pipeline", overview?.jobs_active ? 3000 : 7000);
  const rows = (data || []).filter(
    (j) => f === "all" || (f === "active" && j.active) || (f === "done" && j.state === "done") || (f === "attention" && ["failed", "blocked", "stalled"].includes(j.state)),
  );
  return (
    <div>
      <div className="tools">
        {FILTERS.map(([id, label]) => (
          <button key={id} type="button" className={`act sm${f === id ? "" : " sec"}`} onClick={() => setF(id)}>
            {label}
          </button>
        ))}
      </div>
      {!data ? <p className="mut">Loading…</p> : rows.length ? rows.map((j) => <JobCard key={j.job || j.url} job={j} onWatch={onWatch} />) : <Empty>No jobs in this view yet.</Empty>}
    </div>
  );
}
