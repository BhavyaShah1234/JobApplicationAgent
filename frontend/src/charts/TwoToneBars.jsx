// Bars where part of each is highlighted: {label, n, part} draws n as the pale bar and `part` of it in the strong colour, with "part / n" at the end.
export default function TwoToneBars({ items, label, partName = "kept" }) {
  const m = Math.max(1, ...items.map((x) => x.n));
  if (!items.length) return <p className="mut">Nothing to show yet.</p>;
  return (
    <div className="barlist" role="img" aria-label={`${label}: ${items.map((x) => `${x.label} ${x.part} ${partName} of ${x.n}`).join(", ")}`}>
      {items.map((x) => (
        <div className="brow two" key={x.label}>
          <div className="blabel" title={x.label}>{x.label}</div>
          <div className="btrack">
            <div className="bpale" style={{ width: `${Math.max(2, (100 * x.n) / m)}%` }}>
              <div className="bfill" style={{ width: `${x.n ? (100 * x.part) / x.n : 0}%` }} />
            </div>
          </div>
          <div className="bnum">{x.part} / {x.n.toLocaleString()}</div>
        </div>
      ))}
    </div>
  );
}
