import { useState } from "react";
import { ago, dur, iteration, kind, STATE_TEXT } from "../format.js";
import Chip from "./Chip.jsx";
import { TipBody, useTip } from "../tooltip.jsx";
import StageStep from "./StageStep.jsx";

// One job: title, state, a bar for the whole pipeline, a box per stage, the current step in words, and a history on demand.
const BROWSER_STAGES = ["scrape", "apply", "submit"];                // the stages in which the agent is driving a web page

export default function JobCard({ job: j, onWatch }) {
  const [open, setOpen] = useState(false);
  const cur = j.stages.find((s) => s.id === j.current);
  const done = j.state === "done";
  const it = cur ? iteration(cur) : "";
  const tip = useTip();
  const doneStages = j.stages.filter((s) => s.status === "done" || s.status === "skipped").length;
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
      <div className={`bar hov ${j.state}`} role="progressbar" aria-valuenow={done ? 100 : j.percent} aria-valuemin={0} aria-valuemax={100}
        {...tip(<TipBody title="Whole pipeline" rows={[["Progress", `${done ? 100 : j.percent}%`], ["Stages done", `${doneStages} of ${j.stages.length}`], ["Now", cur ? `${cur.label}${cur.step ? `: ${cur.step}` : ""}${iteration(cur) ? ` ${iteration(cur)}` : ""}` : "–"]]} />, `Pipeline ${done ? 100 : j.percent}%`)}>
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
      <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
        <button type="button" className="act sec sm" onClick={() => setOpen(!open)}>
          {open ? "Hide history" : "History"}
        </button>
        {onWatch && j.state === "running" && BROWSER_STAGES.includes(j.current) && (
          <button type="button" className="act sm" onClick={onWatch}>
            Watch the browser
          </button>
        )}
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
