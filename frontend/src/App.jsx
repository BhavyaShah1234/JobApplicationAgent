import { useEffect, useState } from "react";
import { get } from "./api.js";
import { usePoll } from "./hooks.js";
import Analytics from "./views/Analytics.jsx";
import Browser from "./views/Browser.jsx";
import Jobs from "./views/Jobs.jsx";
import Outcomes from "./views/Outcomes.jsx";
import Overview from "./views/Overview.jsx";
import Questions from "./views/Questions.jsx";
import Queue from "./views/Queue.jsx";
import Review from "./views/Review.jsx";
import Runs from "./views/Runs.jsx";
import StartRun from "./views/StartRun.jsx";

const TABS = [["overview", "Overview"], ["jobs", "Jobs"], ["browser", "Browser"], ["questions", "Questions"], ["review", "Review"], ["queue", "Queue"], ["runs", "Runs"], ["outcomes", "Outcomes"], ["analytics", "Analytics"], ["start", "Start a run"]];

export default function App() {
  const [tab, setTab] = useState((location.hash || "#overview").slice(1));
  const [config, setConfig] = useState({ read_only: false, novnc: "" });
  const [signedOut, setSignedOut] = useState(false);
  const { data: overview, reload } = usePoll("/api/overview", 4000);
  useEffect(() => {
    get("/api/config").then(setConfig).catch(() => {});
    const out = () => setSignedOut(true);
    window.addEventListener("signed-out", out);
    return () => window.removeEventListener("signed-out", out);
  }, []);
  function go(id) {
    setTab(id);
    location.hash = id;
  }
  if (signedOut) return <main><p>Signed out: open the address that `jobagent dashboard` printed.</p></main>;
  const live = overview?.jobs_active > 0;
  const tabs = TABS.filter(([id]) => !(config.read_only && id === "start"));
  return (
    <>
      <header className="top">
        <h1>Job agent</h1>
        <nav role="tablist">
          {tabs.map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => go(id)}>
              {label}
              {id === "questions" && overview?.questions > 0 && <span className="badge">{overview.questions}</span>}
              {id === "review" && overview?.waiting > 0 && <span className="badge info">{overview.waiting}</span>}
              {id === "jobs" && overview?.jobs_active > 0 && <span className="badge info">{overview.jobs_active}</span>}
            </button>
          ))}
        </nav>
        <span className="live">
          <span className={`dot${live ? " on" : ""}`} />
          <span>{live ? "live · " : ""}{overview ? `updated ${new Date().toLocaleTimeString()}` : "connecting…"}</span>
        </span>
      </header>
      <main>
        {tab === "overview" && <Overview overview={overview} config={config} onWatch={() => go("browser")} />}
        {tab === "jobs" && <Jobs overview={overview} onWatch={() => go("browser")} />}
        {tab === "browser" && <Browser config={config} />}
        {tab === "questions" && <Questions config={config} onChange={reload} />}
        {tab === "review" && <Review />}
        {tab === "queue" && <Queue />}
        {tab === "runs" && <Runs />}
        {tab === "outcomes" && <Outcomes />}
        {tab === "analytics" && <Analytics />}
        {tab === "start" && !config.read_only && <StartRun onStarted={() => go("jobs")} />}
      </main>
    </>
  );
}
