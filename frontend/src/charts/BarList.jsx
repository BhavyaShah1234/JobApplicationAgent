import { TipBody, useTip } from "../tooltip.jsx";
import { n0, share } from "./format.js";

// Horizontal bars for a short list of {label, n}. `max` defaults to the largest value; `unit` names what is counted in the tooltip.
export default function BarList({ items, label, max, unit = "" }) {
  const tip = useTip();
  const m = max ?? Math.max(1, ...items.map((x) => x.n));
  const total = items.reduce((a, x) => a + x.n, 0);
  if (!items.length) return <p className="mut">Nothing to show yet.</p>;
  return (
    <div className="barlist" role="img" aria-label={`${label}: ${items.map((x) => `${x.label} ${x.n}`).join(", ")}`}>
      {items.map((x) => (
        <div className="brow hov" key={x.label} {...tip(<TipBody title={x.label} rows={[[unit ? `Count (${unit})` : "Count", n0(x.n)], [`Share of this list`, share(x.n, total)]]} />, `${x.label}: ${x.n}`)}>
          <div className="blabel" title={x.label}>{x.label}</div>
          <div className="btrack">
            <div className="bfill" style={{ width: `${Math.max(x.n ? 2 : 0, (100 * x.n) / m)}%` }} />
          </div>
          <div className="bnum">{n0(x.n)}</div>
        </div>
      ))}
    </div>
  );
}
