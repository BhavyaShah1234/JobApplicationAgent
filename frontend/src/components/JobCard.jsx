import { useState } from "react";
import { ago, dur, iteration, kind, STATE_TEXT } from "../format.js";
import Chip from "./Chip.jsx";
import StageStep from "./StageStep.jsx";

// One job: title, state, a bar for the whole pipeline, a box per stage, the current step in words, and a history on demand.
export default function JobCard({ job: j }) {
  const [open, setOpen] = useState(false);
  const cur = j.stages.find((s) => s.id === j.current);
  const done = j.state === "done";
  const it = cur ? iteration(cur) : "";
  return (
    <div className="job" data-testid="job-card">
      <div className="jh">
        <span className="jt">{j.title || "(untitled)"}</span>
        {j.company && <span className="jc">{j.company}</span>}
        <Chip tone={j.state === "running" ? "run" : kind(j.state)}>{STATE_TEXT[j.state] || j.state}</Chip>
        <span className="jr">
          <span>{j.active ? `running for ${dur(j.elapsed_s)}` : `took ${dur(j.elapsed_s)}`}</span>
          <span>updated {ago(j.updated)}</span>
          {j.url && (
            <a href={j.url} target="_blank" rel="noopener noreferrer">
              posting
            </a>
          )}
        </span>
      </div>
      <div className={`bar ${j.state}`} role="progressbar" aria-valuenow={done ? 100 : j.percent} aria-valuemin={0} aria-valuemax={100}>
        <i style={{ width: `${done ? 100 : j.percent}%` }} />
      </div>
      <div className="steps">
        {j.stages.map((s) => (
          <StageStep key={s.id} stage={s} />
        ))}
      </div>
      {cur && j.state === "running" && (
        <div className="now">
          <b>{cur.label}</b>
          {cur.step ? ` — ${cur.step}${it ? `, step ${it}` : ""}` : ""}
          {cur.detail ? <span className="mut">{`  ·  ${cur.detail}`}</span> : null}
        </div>
      )}
      <div style={{ marginTop: 10 }}>
        <button type="button" className="act sec sm" onClick={() => setOpen(!open)}>
          {open ? "Hide history" : "History"}
        </button>
      </div>
      {open && (
        <ul className="trail">
          {j.trail
            .slice()
            .reverse()
            .map((e, n) => (
              <li key={n}>
                {`${ago(e.ts)} · ${e.stage}${e.label ? ` · ${e.label}` : ""}${e.i != null && e.n ? ` ${e.i}/${e.n}` : ""} · ${e.status}${e.detail ? ` — ${e.detail}` : ""}`}
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}
