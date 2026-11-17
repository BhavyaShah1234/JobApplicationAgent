import { TipBody, useTip } from "../tooltip.jsx";
import { n0, share } from "./format.js";

// Bars where part of each is highlighted: {label, n, part} draws n as the pale bar and `part` of it in the strong colour, with "part / n" at the end.
export default function TwoToneBars({ items, label, partName = "passed the filters", wholeName = "found" }) {
  const tip = useTip();
  const m = Math.max(1, ...items.map((x) => x.n));
  if (!items.length) return <p className="mut">Nothing to show yet.</p>;
  return (
    <div className="barlist" role="img" aria-label={`${label}: ${items.map((x) => `${x.label} ${x.part} ${partName} of ${x.n}`).join(", ")}`}>
      {items.map((x) => (
        <div className="brow two hov" key={x.label} {...tip(<TipBody title={x.label} rows={[[wholeName[0].toUpperCase() + wholeName.slice(1), n0(x.n)], [partName[0].toUpperCase() + partName.slice(1), n0(x.part)], ["Share that passed", share(x.part, x.n)]]} />, `${x.label}: ${x.part} of ${x.n}`)}>
          <div className="blabel" title={x.label}>{x.label}</div>
          <div className="btrack">
            <div className="bpale" style={{ width: `${Math.max(2, (100 * x.n) / m)}%` }}>
              <div className="bfill" style={{ width: `${x.n ? (100 * x.part) / x.n : 0}%` }} />
            </div>
          </div>
          <div className="bnum">{x.part} / {n0(x.n)}</div>
        </div>
      ))}
    </div>
  );
}
