import { usePoll } from "../hooks.js";
import Chip from "../components/Chip.jsx";
import Empty from "../components/Empty.jsx";
import JobCard from "../components/JobCard.jsx";

function Totals({ title, data }) {
  return (
    <div className="box">
      <div>
        <b>{title}</b>{" "}
        {Object.entries(data || {}).map(([k, v]) => (
          <span key={k} style={{ marginRight: 12 }}>
            <Chip>{k}</Chip> {v}
          </span>
        ))}
      </div>
    </div>
  );
}

function Stat({ n, label, hot }) {
  return (
    <div className={`card${hot && n ? " hot" : ""}`}>
      <b>{n}</b>
      <span>{label}</span>
    </div>
  );
}

export default function Overview({ overview: o, config, onWatch }) {
  const { data: jobs } = usePoll("/api/pipeline", o?.jobs_active ? 3000 : 7000);
  if (!o) return <p className="mut">Loading…</p>;
  const live = (jobs || []).filter((j) => j.active);
  const recent = (jobs || []).filter((j) => !j.active).slice(0, 4);
  return (
    <div>
      {config.read_only && <div className="box">Read-only view: you can look at everything here, but answering questions and starting runs is done in a terminal.</div>}
      <div className="cards">
        <Stat n={o.questions} label="questions waiting for you" hot />
        <Stat n={o.waiting} label="applications to review" />
        <Stat n={o.ready} label="of them ready to send" />
        <Stat n={o.jobs_active || 0} label="jobs working now" />
      </div>
      <h2>Working now</h2>
      {live.length ? live.map((j) => <JobCard key={j.job || j.url} job={j} onWatch={onWatch} />) : <Empty>Nothing is running. Start a run from the Start tab or `jobagent run` in a terminal.</Empty>}
      {recent.length > 0 && (
        <>
          <h2>Recently finished</h2>
          {recent.map((j) => (
            <JobCard key={j.job || j.url} job={j} />
          ))}
        </>
      )}
      <h2>Totals</h2>
      <Totals title="Queue" data={o.jobs} />
      <Totals title="Applications" data={o.applications} />
      <Totals title="Outcomes" data={o.outcomes} />
      {config.novnc && (
        <p>
          Browser view:{" "}
          <a href={config.novnc} target="_blank" rel="noopener noreferrer">
            {config.novnc}
          </a>
        </p>
      )}
    </div>
  );
}
