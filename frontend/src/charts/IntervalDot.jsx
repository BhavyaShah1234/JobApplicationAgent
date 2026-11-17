import { TipBody, useTip } from "../tooltip.jsx";

// A rate with its confidence interval on a 0-100% line. A group with too few resolved applications is drawn grey with no claim.
// `k` and `n` (replied of resolved) and `name` make the tooltip say what the rate is made of.
export default function IntervalDot({ rate, ci, tooFew, k, n, name }) {
  const tip = useTip();
  if (rate == null || !ci) return <span className="mut">–</span>;
  const pct = (v) => `${Math.round(v * 100)}%`;
  const rows = [["Rate", pct(rate)], ["95% interval", `${pct(ci[0])} to ${pct(ci[1])}`]];
  if (n != null) rows.unshift(["Replied of resolved", `${k} of ${n}`]);
  return (
    <span className={`interval${tooFew ? " few" : ""}`} {...tip(<TipBody title={name} rows={rows} note={tooFew ? "Too few resolved applications to trust this rate." : "Wide intervals mean little data."} />, `${pct(rate)}, 95% interval ${pct(ci[0])} to ${pct(ci[1])}${tooFew ? ", too few to trust" : ""}`)}>
      <span className="itrack">
        <span className="ispan" style={{ left: `${ci[0] * 100}%`, width: `${Math.max(1, (ci[1] - ci[0]) * 100)}%` }} />
        <span className="idot" style={{ left: `${rate * 100}%` }} />
      </span>
      <span className="ival">{pct(rate)}</span>
    </span>
  );
}
