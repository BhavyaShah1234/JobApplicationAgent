import { useEffect, useState } from "react";
import { get } from "./api.js";
import { usePoll } from "./hooks.js";
import Browser from "./views/Browser.jsx";
import Jobs from "./views/Jobs.jsx";
import Outcomes from "./views/Outcomes.jsx";
import Overview from "./views/Overview.jsx";
import Questions from "./views/Questions.jsx";
import Queue from "./views/Queue.jsx";
import Review from "./views/Review.jsx";
import Runs from "./views/Runs.jsx";
import FetchError from "./components/FetchError.jsx";
import PresenceToggle from "./components/PresenceToggle.jsx";

const TABS = [["overview", "Overview"], ["jobs", "Jobs"], ["browser", "Browser"], ["questions", "Questions"], ["review", "Review"], ["queue", "Queue"], ["runs", "Runs"], ["outcomes", "Outcomes"]];

function currentTab() {
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const legacy = ["/", "/index.html"].includes(path) ? location.hash.slice(1) : "";
  const raw = legacy || path.slice(1);
  const id = raw === "start" ? "queue" : raw;                       // the old Start a run page now lives on the Queue page
  return TABS.some(([key]) => key === id) ? id : "overview";
}

export default function App() {
  const [tab, setTab] = useState(currentTab);
  const [config, setConfig] = useState({ read_only: false, novnc: "" });
  const [signedOut, setSignedOut] = useState(false);
  const { data: overview, error: overviewError, updatedAt, reload } = usePoll("/api/overview", 4000);
  useEffect(() => {
    const changed = () => {
      const id = currentTab();
      const path = "/" + id;
      if (location.pathname !== path || location.hash) history.replaceState(history.state, "", path);
      setTab(id);
    };
    changed();
    window.addEventListener('popstate', changed);
    window.addEventListener('hashchange', changed);
    return () => {
      window.removeEventListener('popstate', changed);
      window.removeEventListener('hashchange', changed);
    };
  }, []);
  useEffect(() => {
    get("/api/config").then(setConfig).catch(() => {});
    const out = () => setSignedOut(true);
    window.addEventListener("signed-out", out);
    return () => window.removeEventListener("signed-out", out);
  }, []);
  function go(id) {
    const path = "/" + id;
    if (location.pathname !== path) history.pushState(null, "", path);
    setTab(id);
  }
  if (signedOut) return <main><p>Signed out: open the address that `jobagent dashboard` printed.</p></main>;
  const live = overview?.jobs_active > 0;
  const tabs = TABS;
  return (
    <>
      <header className="top">
        <h1>Job agent</h1>
        <nav role="tablist">
          {tabs.map(([id, label]) => (
            <a key={id} href={"/" + id} role="tab" aria-selected={tab === id} onClick={(event) => {
              if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
                event.preventDefault();
                go(id);
              }
            }}>
              {label}
              {id === "questions" && overview?.questions > 0 && <span className="badge">{overview.questions}</span>}
              {id === "review" && overview?.waiting > 0 && <span className="badge info">{overview.waiting}</span>}
              {id === "jobs" && overview?.jobs_active > 0 && <span className="badge info">{overview.jobs_active}</span>}
            </a>
          ))}
        </nav>
        <PresenceToggle presence={overview?.presence} deferred={overview?.deferred || 0} readOnly={config.read_only} onChange={reload} />
        <span className="live">
          <span className={`dot${live ? " on" : ""}`} />
          <span>{live && !overviewError ? "live · " : ""}{updatedAt ? `${overviewError ? 'last update' : 'updated'} ${new Date(updatedAt).toLocaleTimeString()}` : "connecting…"}</span>
        </span>
      </header>
      <main>
        <FetchError error={overviewError} reload={reload} />
        {tab === "overview" && <Overview overview={overview} config={config} onWatch={() => go("browser")} />}
        {tab === "jobs" && <Jobs overview={overview} onWatch={() => go("browser")} />}
        {tab === "browser" && <Browser config={config} />}
        {tab === "questions" && <Questions config={config} onChange={reload} />}
        {tab === "review" && <Review />}
        {tab === "queue" && <Queue config={config} onOpenJobs={() => go("jobs")} />}
        {tab === "runs" && <Runs />}
        {tab === "outcomes" && <Outcomes />}
      </main>
    </>
  );
}
