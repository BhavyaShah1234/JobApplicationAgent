import { useState } from "react";
import { ago } from "../format.js";
import { usePoll } from "../hooks.js";
import Chip from "../components/Chip.jsx";
import DataTable from "../components/DataTable.jsx";
import FetchError from "../components/FetchError.jsx";

function RunLog({ id }) {
  const [only, setOnly] = useState(false);
  const { data, error, reload } = usePoll(`/api/run/${encodeURIComponent(id)}`, 4000);
  const rows = (data || []).filter((e) => !only || ["error", "warn"].includes(e.level));
  return (
    <div>
      <FetchError error={error} reload={reload} />
      <div className="tools">
        <b>{id}</b>
        <button type="button" className={`act sm${only ? " sec" : ""}`} onClick={() => setOnly(false)}>
          All
        </button>
        <button type="button" className={`act sm${only ? "" : " sec"}`} onClick={() => setOnly(true)}>
          Problems only
        </button>
      </div>
      <div className="log">
        {rows.map((e, i) => (
          <div key={i} className={e.level}>{`${e.ts.slice(11, 19)} ${e.level.padEnd(8)} [${e.step}] ${e.msg}`}</div>
        ))}
      </div>
    </div>
  );
}

function stateChip(x) {
  const text = x.state === "waiting" ? `waiting ${Math.round(x.quiet_s / 60)} min` : x.state;
  const tone = x.state === "running" ? "run" : x.state === "finished" ? "ok" : ["stalled", "failed", "blocked"].includes(x.state) ? "bad" : "warn";
  return <Chip tone={tone}>{text}</Chip>;
}

export default function Runs() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(null);
  const { data, error, reload } = usePoll("/api/runs", 6000);
  if (!data) return <><FetchError error={error} reload={reload} /><p className="mut">Loading…</p></>;
  const needle = q.toLowerCase();
  const rows = data.filter((x) => !needle || `${x.step} ${x.job} ${x.message}`.toLowerCase().includes(needle));
  return (
    <div>
      <FetchError error={error} reload={reload} />
      <div className="tools">
        <input type="search" placeholder="Filter by step, job or message" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="mut">{data.length} recent runs</span>
      </div>
      <DataTable
        cols={["Step", "Job", "Started", "State", "Lines", "Errors", "Last line", ""]}
        rows={rows}
        rowKey={(x) => x.id}
        render={(x) => [
          x.step,
          x.job,
          ago(x.started),
          stateChip(x),
          x.lines,
          x.errors ? <Chip key="e" tone="bad">{String(x.errors)}</Chip> : "0",
          <span key="m" className="mut">{x.message}</span>,
          <button key="l" type="button" className="act sec sm" onClick={() => setOpen(x.id)}>
            Log
          </button>,
        ]}
      />
      {open && <RunLog key={open} id={open} />}
    </div>
  );
}
