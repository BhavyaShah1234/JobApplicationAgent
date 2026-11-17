// Horizontal bars for a short list of {label, n}. `max` defaults to the largest value.
export default function BarList({ items, label, max }) {
  const m = max ?? Math.max(1, ...items.map((x) => x.n));
  if (!items.length) return <p className="mut">Nothing to show yet.</p>;
  return (
    <div className="barlist" role="img" aria-label={`${label}: ${items.map((x) => `${x.label} ${x.n}`).join(", ")}`}>
      {items.map((x) => (
        <div className="brow" key={x.label}>
          <div className="blabel" title={x.label}>{x.label}</div>
          <div className="btrack">
            <div className="bfill" style={{ width: `${Math.max(x.n ? 2 : 0, (100 * x.n) / m)}%` }} />
          </div>
          <div className="bnum">{x.n.toLocaleString()}</div>
        </div>
      ))}
    </div>
  );
}
