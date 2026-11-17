// A funnel as bars: each step's width is its count relative to the first, with the share kept from the step before it.
export default function FunnelChart({ steps }) {
  const top = Math.max(1, ...steps.map((s) => s.n));
  return (
    <div className="funnel" role="img" aria-label={`Funnel: ${steps.map((s) => `${s.label} ${s.n}`).join(", ")}`}>
      {steps.map((s, i) => {
        const prev = i ? steps[i - 1].n : null;
        const keep = prev ? Math.round((100 * s.n) / prev) : null;
        return (
          <div className="frow" key={s.id}>
            <div className="flabel">{s.label}</div>
            <div className="ftrack">
              <div className="fbar" style={{ width: `${Math.max(s.n ? 2 : 0, (100 * s.n) / top)}%` }} />
            </div>
            <div className="fnum">
              <b>{s.n.toLocaleString()}</b>
              {keep != null && <span className="mut"> {keep}% of previous</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
