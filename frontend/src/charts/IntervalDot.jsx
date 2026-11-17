// A rate with its confidence interval on a 0-100% line. A group with too few resolved applications is drawn grey with no claim.
export default function IntervalDot({ rate, ci, tooFew }) {
  if (rate == null || !ci) return <span className="mut">–</span>;
  const pct = (v) => `${Math.round(v * 100)}%`;
  return (
    <span className={`interval${tooFew ? " few" : ""}`} title={`${pct(rate)} (95% interval ${pct(ci[0])} to ${pct(ci[1])})${tooFew ? "; too few to trust" : ""}`}>
      <span className="itrack">
        <span className="ispan" style={{ left: `${ci[0] * 100}%`, width: `${Math.max(1, (ci[1] - ci[0]) * 100)}%` }} />
        <span className="idot" style={{ left: `${rate * 100}%` }} />
      </span>
      <span className="ival">{pct(rate)}</span>
    </span>
  );
}
