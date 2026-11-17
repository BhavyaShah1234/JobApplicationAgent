import { useState } from "react";
import { merge, TipBody, useTip } from "../tooltip.jsx";
import { n0, share } from "./format.js";

// Vertical bars for an ordered series of {label, n} (a score distribution). Hovering a bar dims the others and shows its count and share.
export default function Histogram({ bins, label, unit = "" }) {
  const tip = useTip();
  const [on, setOn] = useState(null);
  const m = Math.max(1, ...bins.map((b) => b.n));
  const total = bins.reduce((a, b) => a + b.n, 0);
  if (!total) return <p className="mut">Nothing to show yet.</p>;
  return (
    <div className={`hist${on != null ? " has-active" : ""}`} role="img" aria-label={`${label}: ${bins.map((b) => `${b.label} ${b.n}`).join(", ")}`} onMouseLeave={() => setOn(null)}>
      {bins.map((b, i) => (
        <div
          className={`hcol hov${on === i ? " active" : ""}`}
          key={b.label}
          {...merge(tip(<TipBody title={b.label} rows={[[unit ? `Count (${unit})` : "Count", n0(b.n)], ["Share of all", share(b.n, total)]]} />, `${b.label}: ${b.n}`), {
            onMouseEnter: () => setOn(i), onFocus: () => setOn(i), onBlur: () => setOn(null),
          })}
        >
          <div className="hn">{b.n || ""}</div>
          <div className="hbar" style={{ height: `${Math.max(b.n ? 4 : 0, (100 * b.n) / m)}%` }} />
          <div className="hl">{b.label}</div>
        </div>
      ))}
    </div>
  );
}
