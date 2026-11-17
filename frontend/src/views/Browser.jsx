import { useState } from "react";
import { usePoll } from "../hooks.js";
import Chip from "../components/Chip.jsx";
import FetchError from "../components/FetchError.jsx";

// The agent's own browser, live, inside the dashboard: noVNC (served by the agent's container) in a frame. It is the same view you get at the
// noVNC address, so it also lets you solve a CAPTCHA or take over a form here; a read-only dashboard asks noVNC for a view-only session.
export function embedUrl(url, readOnly) {
  const u = new URL(url);
  const q = new URLSearchParams({ autoconnect: "true", reconnect: "true", resize: "scale" });
  if (readOnly) q.set("view_only", "true");
  u.search = q.toString();
  return u.toString();
}

export default function Browser({ config }) {
  const { data, error, reload } = usePoll("/api/vnc", 6000);
  const { data: jobs } = usePoll('/api/pipeline', 6000);
  const [nonce, setNonce] = useState(0);
  const url = config.novnc;
  if (!url) {
    return (
      <div className="box">
        <p>No browser view is set up. Start the dashboard with <code>--novnc-url http://localhost:6080/vnc.html</code> (or <code>NOVNC_URL=… scripts/dashboard.sh start</code>).</p>
      </div>
    );
  }
  return (
    <div>
      <FetchError error={error} reload={reload} />
      {(jobs || []).some(j => j.active && j.browser_mode === 'desktop') && <div className="box warn">The agent is using its dedicated host Chrome window. Switch to that window to watch or answer a human check; the Docker desktop below may be blank.</div>}
      <div className="tools">
        <b>Agent browser</b>
        {data && <Chip tone={data.reachable ? "ok" : "bad"}>{data.reachable ? "connected" : "not reachable"}</Chip>}
        <button type="button" className="act sec sm" onClick={() => setNonce(nonce + 1)}>
          Reload
        </button>
        <a href={url} target="_blank" rel="noopener noreferrer">
          Open in its own tab
        </a>
        {config.read_only && <span className="mut">view only</span>}
      </div>
      {data && !data.reachable ? (
        <div className="box">
          <p className="bad">Nothing answers at {url}.</p>
          <p className="mut">The agent's container serves its browser on this address (noVNC, port 6080 by default; another container may use 6082). Check that it is running, or start the dashboard with the right <code>--novnc-url</code>.</p>
        </div>
      ) : (
        <iframe
          key={nonce}
          title="Agent browser"
          className="vnc"
          src={embedUrl(url, config.read_only)}
          sandbox="allow-scripts allow-same-origin"
          allow="clipboard-read; clipboard-write"
        />
      )}
    </div>
  );
}
