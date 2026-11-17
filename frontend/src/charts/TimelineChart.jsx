import { useState } from "react";
import { TipBody, useTip, useWidth } from "../tooltip.jsx";

// Daily counts as grouped bars in SVG: found, filled, submitted, responses. `days` is [{day, found, filled, submitted, responses}].
// It draws at its real width (crisp text on a phone too). Point at a day, or focus the chart and use the arrow keys, for that day's numbers.
const SERIES = [
  ["found", "Postings found", "var(--line)"],
  ["filled", "Filled to review", "var(--acc)"],
  ["submitted", "Submitted", "var(--ok)"],
  ["responses", "Responses", "var(--warn)"],
];

export default function TimelineChart({ days, show = SERIES.map((s) => s[0]) }) {
  const [ref, W] = useWidth(760);
  const tip = useTip();
  const [at, setAt] = useState(null);
  const H = 190, L = 34, B = 24, T = 8;
  const series = SERIES.filter((s) => show.includes(s[0]));
  const max = Math.max(1, ...days.flatMap((d) => series.map((s) => d[s[0]])));
  const step = (W - L) / Math.max(1, days.length);
  const bw = Math.max(1.5, (step - 2) / series.length);
  const y = (v) => T + (H - T - B) * (1 - v / max);
  const ticks = [...new Set([0, Math.ceil(max / 2), max])];
  const total = days.reduce((a, d) => a + series.reduce((b, s) => b + d[s[0]], 0), 0);
  if (!days.length || !total) return <p className="mut">No activity in this period.</p>;
  const body = (d) => <TipBody title={d.day} rows={series.map(([k, l]) => [l, d[k]])} />;
  const labelAt = [0, Math.floor(days.length / 2), days.length - 1].filter((i, k, a) => a.indexOf(i) === k && (W > 420 || i !== Math.floor(days.length / 2)));
  const move = (i) => setAt(Math.max(0, Math.min(days.length - 1, i)));
  return (
    <div ref={ref}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width={W}
        height={H}
        className="timeline"
        role="img"
        aria-label={`Daily activity over ${days.length} days. Use the arrow keys to move between days.`}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") { e.preventDefault(); move((at ?? -1) + 1); }
          if (e.key === "ArrowLeft") { e.preventDefault(); move((at ?? days.length) - 1); }
          if (e.key === "Escape") setAt(null);
        }}
        onBlur={() => setAt(null)}
        onMouseLeave={() => setAt(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W} y1={y(t)} y2={y(t)} className="grid" />
            <text x={L - 6} y={y(t) + 4} textAnchor="end" className="axis">{t}</text>
          </g>
        ))}
        {at != null && <rect x={L + at * step} y={T} width={step} height={H - T - B} className="guide" />}
        {days.map((d, i) =>
          series.map(([key, label, color], k) =>
            d[key] ? (
              <rect key={`${d.day}-${key}`} x={L + i * step + k * bw} y={y(d[key])} width={bw} height={H - B - y(d[key])} fill={color} rx="1" className={at === i ? "on" : ""}>
                <title>{`${d.day}: ${d[key]} ${label.toLowerCase()}`}</title>
              </rect>
            ) : null,
          ),
        )}
        {days.map((d, i) => (
          <rect
            key={`hit-${d.day}`}
            x={L + i * step}
            y={T}
            width={step}
            height={H - T - B}
            fill="transparent"
            data-testid="day-hit"
            onMouseEnter={(e) => { setAt(i); tip(body(d)).onMouseEnter?.(e); }}
            onMouseMove={(e) => tip(body(d)).onMouseMove?.(e)}
            onMouseLeave={(e) => { setAt(null); tip(body(d)).onMouseLeave?.(e); }}
            onClick={(e) => { setAt(i); tip(body(d)).onClick?.(e); }}
          />
        ))}
        {labelAt.map((i) => (
          <text key={i} x={L + i * step} y={H - 6} className="axis" textAnchor={i === 0 ? "start" : i === days.length - 1 ? "end" : "middle"}>
            {days[i].day.slice(5)}
          </text>
        ))}
      </svg>
      {at != null && <div className="sr-only" aria-live="polite">{`${days[at].day}: ${series.map(([k, l]) => `${days[at][k]} ${l.toLowerCase()}`).join(", ")}`}</div>}
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
