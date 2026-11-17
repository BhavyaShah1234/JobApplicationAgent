import { TipBody, useTip } from "../tooltip.jsx";
import { n0, share } from "./format.js";

// A funnel as bars: each step's width is its count relative to the first, with the share kept from the step before it. Hover (or focus, or tap) a step
// for the full picture: its share of everything found, what it kept from the step above, and how many dropped out here.
export default function FunnelChart({ steps }) {
  const tip = useTip();
  const top = Math.max(1, ...steps.map((s) => s.n));
  return (
    <div className="funnel" role="img" aria-label={`Funnel: ${steps.map((s) => `${s.label} ${s.n}`).join(", ")}`}>
      {steps.map((s, i) => {
        const prev = i ? steps[i - 1].n : null;
        const keep = prev ? Math.round((100 * s.n) / prev) : null;
        const rows = [["Count", n0(s.n)], ["Share of postings found", share(s.n, steps[0].n)]];
        if (prev != null) rows.push(["Kept from the step above", share(s.n, prev)], ["Dropped out here", n0(Math.max(0, prev - s.n))]);
        return (
          <div className="frow hov" key={s.id} {...tip(<TipBody title={s.label} rows={rows} />, `${s.label}: ${s.n}`)}>
            <div className="flabel">{s.label}</div>
            <div className="ftrack">
              <div className="fbar" style={{ width: `${Math.max(s.n ? 2 : 0, (100 * s.n) / top)}%` }} />
            </div>
            <div className="fnum">
              <b>{n0(s.n)}</b>
              {keep != null && <span className="mut"> {keep}% of previous</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
