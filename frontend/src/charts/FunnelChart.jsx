import { TipBody, useTip } from "../tooltip.jsx";
import { n0 } from "./format.js";

// Milestone counts can have different cohorts; do not invent conversion rates.
export default function FunnelChart({ steps }) {
  const tip = useTip();
  const top = Math.max(1, ...steps.map((s) => s.n));
  return (
    <div className="funnel" role="img" aria-label={`Funnel: ${steps.map((s) => `${s.label} ${s.n}`).join(", ")}`}>
      {steps.map((s) => {
        const rows = [["Recorded count", n0(s.n)]];
        return (
          <div className="frow hov" key={s.id} {...tip(<TipBody title={s.label} rows={rows} />, `${s.label}: ${s.n}`)}>
            <div className="flabel">{s.label}</div>
            <div className="ftrack">
              <div className="fbar" style={{ width: `${Math.max(s.n ? 2 : 0, (100 * s.n) / top)}%` }} />
            </div>
            <div className="fnum">
              <b>{n0(s.n)}</b>
            </div>
          </div>
        );
      })}
    </div>
  );
}
