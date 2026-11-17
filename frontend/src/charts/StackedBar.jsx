// One bar split into labelled parts that add up to the whole, with a legend that gives every part's count and share.
export default function StackedBar({ segments, label }) {
  const parts = segments.filter((s) => s.n > 0);
  const total = parts.reduce((a, s) => a + s.n, 0);
  if (!total) return <p className="mut">Nothing to show yet.</p>;
  const pct = (n) => (n / total < 0.01 ? "<1%" : `${Math.round((100 * n) / total)}%`);
  return (
    <div>
      <div className="stack" role="img" aria-label={`${label}: ${parts.map((s) => `${s.label} ${s.n}`).join(", ")}`}>
        {parts.map((s, i) => (
          <div key={s.label} className={`seg c${i % 8}`} style={{ flexGrow: s.n }} title={`${s.label}: ${s.n.toLocaleString()} (${pct(s.n)})`} />
        ))}
      </div>
      <ul className="slegend">
        {parts.map((s, i) => (
          <li key={s.label}>
            <i className={`sw c${i % 8}`} />
            <span className="sl">{s.label}</span>
            <span className="sn">{s.n.toLocaleString()}</span>
            <span className="sp mut">{pct(s.n)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
