import { useState } from "react";
import { merge, TipBody, useTip } from "../tooltip.jsx";
import { n0, share } from "./format.js";

// One bar split into labelled parts that add up to the whole, with a legend that gives every part's count and share. A part and its legend row
// light up together, whichever you point at.
export default function StackedBar({ segments, label }) {
  const tip = useTip();
  const [on, setOn] = useState(null);
  const parts = segments.filter((s) => s.n > 0);
  const total = parts.reduce((a, s) => a + s.n, 0);
  if (!total) return <p className="mut">Nothing to show yet.</p>;
  const body = (s) => <TipBody title={s.label} rows={[["Count", n0(s.n)], ["Share of all", share(s.n, total)]]} />;
  const hover = (i) => ({ onMouseEnter: () => setOn(i), onMouseLeave: () => setOn(null), onFocus: () => setOn(i), onBlur: () => setOn(null) });
  const lit = (i, base = {}) => merge(base, hover(i));
  return (
    <div className={on != null ? "has-active" : ""}>
      <div className="stack" role="img" aria-label={`${label}: ${parts.map((s) => `${s.label} ${s.n}`).join(", ")}`}>
        {parts.map((s, i) => (
          <div key={s.label} className={`seg c${i % 8}${on === i ? " active" : ""}`} style={{ flexGrow: s.n }} {...lit(i, tip(body(s), `${s.label}: ${s.n}`))} />
        ))}
      </div>
      <ul className="slegend">
        {parts.map((s, i) => (
          <li key={s.label} className={on === i ? "active" : ""} {...lit(i)}>
            <i className={`sw c${i % 8}`} />
            <span className="sl">{s.label}</span>
            <span className="sn">{n0(s.n)}</span>
            <span className="sp mut">{share(s.n, total)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
