// Vertical bars for an ordered series of {label, n} (a score distribution).
export default function Histogram({ bins, label }) {
  const m = Math.max(1, ...bins.map((b) => b.n));
  const total = bins.reduce((a, b) => a + b.n, 0);
  if (!total) return <p className="mut">Nothing to show yet.</p>;
  return (
    <div className="hist" role="img" aria-label={`${label}: ${bins.map((b) => `${b.label} ${b.n}`).join(", ")}`}>
      {bins.map((b) => (
        <div className="hcol" key={b.label}>
          <div className="hn">{b.n || ""}</div>
          <div className="hbar" style={{ height: `${Math.max(b.n ? 4 : 0, (100 * b.n) / m)}%` }} />
          <div className="hl">{b.label}</div>
        </div>
      ))}
    </div>
  );
}
