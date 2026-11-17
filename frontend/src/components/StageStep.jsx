import { ago, ICON, iteration } from "../format.js";
import { TipBody, useTip } from "../tooltip.jsx";

// One stage of a job's pipeline: its state, and while it runs the step inside it and which iteration it is on.
export default function StageStep({ stage: s }) {
  const live = ["running", "stalled", "failed", "blocked"].includes(s.status);
  const it = iteration(s);
  const tip = useTip();
  const rows = [["Status", s.status]];
  if (s.step) rows.push(["Step", it ? `${s.step} (${it})` : s.step]);
  if (s.ts) rows.push(["Since", ago(s.ts)]);
  return (
    <div className={`step hov ${s.status}`} data-testid={`stage-${s.id}`} {...tip(<TipBody title={s.label} rows={rows} note={s.detail} />, `${s.label}: ${s.status}`)}>
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
