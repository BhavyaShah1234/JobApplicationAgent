// Daily counts as grouped bars in SVG: found, filled, submitted, responses. `days` is [{day, found, filled, submitted, responses}].
const SERIES = [
  ["found", "Postings found", "var(--line)"],
  ["filled", "Filled to review", "var(--acc)"],
  ["submitted", "Submitted", "var(--ok)"],
  ["responses", "Responses", "var(--warn)"],
];

export default function TimelineChart({ days, show = SERIES.map((s) => s[0]) }) {
  const W = 760, H = 190, L = 34, B = 24, T = 8;
  const series = SERIES.filter((s) => show.includes(s[0]));
  const max = Math.max(1, ...days.flatMap((d) => series.map((s) => d[s[0]])));
  const step = (W - L) / Math.max(1, days.length);
  const bw = Math.max(1.5, (step - 2) / series.length);
  const y = (v) => T + (H - T - B) * (1 - v / max);
  const ticks = [0, Math.ceil(max / 2), max];
  const total = days.reduce((a, d) => a + series.reduce((b, s) => b + d[s[0]], 0), 0);
  if (!days.length || !total) return <p className="mut">No activity in this period.</p>;
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="timeline" role="img" aria-label={`Daily activity over ${days.length} days`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W} y1={y(t)} y2={y(t)} className="grid" />
            <text x={L - 6} y={y(t) + 4} textAnchor="end" className="axis">{t}</text>
          </g>
        ))}
        {days.map((d, i) =>
          series.map(([key, label, color], k) =>
            d[key] ? (
              <rect key={`${d.day}-${key}`} x={L + i * step + k * bw} y={y(d[key])} width={bw} height={H - B - y(d[key])} fill={color} rx="1">
                <title>{`${d.day}: ${d[key]} ${label.toLowerCase()}`}</title>
              </rect>
            ) : null,
          ),
        )}
        {[0, Math.floor(days.length / 2), days.length - 1].map((i) => (
          <text key={i} x={L + i * step} y={H - 6} className="axis" textAnchor={i === 0 ? "start" : i === days.length - 1 ? "end" : "middle"}>
            {days[i].day.slice(5)}
          </text>
        ))}
      </svg>
      <div className="legend">
        {series.map(([key, label, color]) => (
          <span key={key}>
            <i style={{ background: color }} /> {label}
          </span>
        ))}
      </div>
    </div>
  );
}
