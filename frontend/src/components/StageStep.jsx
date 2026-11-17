import { ICON, iteration } from "../format.js";

// One stage of a job's pipeline: its state, and while it runs the step inside it and which iteration it is on.
export default function StageStep({ stage: s }) {
  const live = ["running", "stalled", "failed", "blocked"].includes(s.status);
  const it = iteration(s);
  return (
    <div className={`step ${s.status}`} data-testid={`stage-${s.id}`}>
      <div className="n">
        <span className={`ico${s.status === "running" ? " spin" : ""}`}>{ICON[s.status] || "○"}</span>
        {s.label}
      </div>
      {live && s.step && <div className="s">{it ? `${s.step} ${it}` : s.step}</div>}
      {(live || s.status === "done") && s.detail && <div className="s">{s.detail}</div>}
      {s.status === "running" && s.i != null && s.n ? (
        <div className="mini">
          <i style={{ width: `${Math.min(100, Math.round((100 * s.i) / s.n))}%` }} />
        </div>
      ) : null}
    </div>
  );
}
