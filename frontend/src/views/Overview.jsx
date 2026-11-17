import { usePoll } from "../hooks.js";
import Empty from "../components/Empty.jsx";
import JobCard from "../components/JobCard.jsx";
import Stat from "../components/Stat.jsx";
import ActivitySection from "./overview/Activity.jsx";
import DiscoverySection from "./overview/Discovery.jsx";
import FunnelSection from "./overview/Funnel.jsx";
import OutcomesSection from "./overview/Outcomes.jsx";
import ScoresSection from "./overview/Scores.jsx";
import { hours, pct } from "./overview/numbers.js";

// Everything at a glance, top to bottom: what needs you, what is running, then the numbers behind the whole pipeline.
export default function Overview({ overview: o, config, onWatch }) {
  const { data: jobs } = usePoll("/api/pipeline", o?.jobs_active ? 3000 : 7000);
  const { data: a } = usePoll("/api/analytics", 20000);
  if (!o) return <p className="mut">Loading…</p>;
  const live = (jobs || []).filter((j) => j.active);
  const recent = (jobs || []).filter((j) => !j.active).slice(0, 3);
  const haveNumbers = a && a.funnel.length > 0;
  const submitted = a?.totals.submitted ?? 0;
  return (
    <div>
      {config.read_only && <div className="box">Read-only view: you can look at everything here, but answering questions and starting runs is done in a terminal.</div>}
      {haveNumbers && submitted < a.outcomes.min_group && (
        <div className="notice">
          Only {submitted} application{submitted === 1 ? "" : "s"} submitted so far. Response rates need at least {a.outcomes.min_group} resolved applications per group, so the outcome figures below describe
          what happened, not what to expect. The discovery numbers are solid.
        </div>
      )}
      <div className="cards">
        <Stat n={o.questions} label="questions waiting for you" hot hint="Things the agent stopped to ask you. Answer them in the Questions tab (or a terminal for secrets)." />
        <Stat n={o.waiting} label="applications to review" sub={`${o.ready} ready to send`} hint="Forms filled to their review screen, waiting for your typed approval. Nothing is sent without it." />
        <Stat n={o.jobs_active || 0} label="jobs working now" hint="Jobs with a run that is writing to its log right now." />
        {haveNumbers && (
          <>
            <Stat n={a.totals.jobs.toLocaleString()} label="postings found" sub={`${a.funnel[1].n.toLocaleString()} passed the filters`} hint="Every posting discovery has seen; the sub-line is how many passed the US and role filters." />
            <Stat n={submitted} label="submitted" sub={`${a.funnel[4].n} receipt confirmed`} hint="Applications you approved and the agent sent. A receipt is confirmed when the employer's email arrives." />
            <Stat
              n={a.outcomes.rate ? pct(a.outcomes.rate.k / a.outcomes.rate.n) : "–"}
              label="of resolved applications got a reply" hint="Of the applications with an answer (a reply, or silence past the cutoff), the share that got a real reply. Silence inside the cutoff is not counted as a no."
              sub={a.outcomes.rate ? `${a.outcomes.rate.k} of ${a.outcomes.rate.n}; 95% interval ${pct(a.outcomes.rate.lo)}–${pct(a.outcomes.rate.hi)}` : "none resolved yet"}
            />
            <Stat n={hours(a.latency.fill_to_submit_h)} label="median wait for your approval" hint="Median time from a form being filled to you sending it." sub={a.latency.n_submit ? `${a.latency.n_submit} applications` : ""} />
          </>
        )}
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

      {haveNumbers ? (
        <>
          <FunnelSection a={a} />
          <ActivitySection a={a} />
          <DiscoverySection a={a} />
          <OutcomesSection a={a} />
          <ScoresSection a={a} />
        </>
      ) : a ? (
        <Empty>No numbers yet: they appear once postings have been discovered.</Empty>
      ) : null}
      {config.novnc && (
        <p style={{ marginTop: 18 }}>
          Browser view: <a href={config.novnc} target="_blank" rel="noopener noreferrer">{config.novnc}</a>
        </p>
      )}
    </div>
  );
}
