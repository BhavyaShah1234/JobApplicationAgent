import { useState } from "react";
import { post } from "../api.js";

// "I'm available / I'm away" for the agent's questions. Available: the agent asks and waits, as before. Away: it does not ask;
// a job that needs you goes back to the end of the queue and the agent moves on, resuming those jobs when you switch back.
export default function PresenceToggle({ presence, deferred = 0, readOnly = false, onChange }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [optimistic, setOptimistic] = useState(null);
  const available = optimistic ?? presence?.available ?? true;
  const known = presence != null;

  async function toggle() {
    if (readOnly || busy || !known) return;
    const next = !available;
    setBusy(true);
    setError("");
    setOptimistic(next);
    const r = await post("/api/presence", { available: next });
    if (!r.ok) {
      setOptimistic(null);
      setError(r.body?.error || "could not change it");
    } else {
      onChange?.();
    }
    setOptimistic(null);
    setBusy(false);
  }

  const title = readOnly
    ? "This dashboard is read-only: use `jobagent hitl away` / `jobagent hitl back` in a terminal"
    : available
      ? "The agent asks you when it needs you. Switch off when you are away: jobs that need you wait at the end of the queue."
      : "The agent does not ask you. Jobs that need you wait at the end of the queue and resume when you switch this on.";
  return (
    <span className="presence">
      <button
        type="button"
        role="switch"
        aria-checked={available}
        aria-label="Available for questions"
        className={`switch${available ? " on" : ""}`}
        disabled={readOnly || busy || !known}
        title={title}
        onClick={toggle}
      >
        <i aria-hidden="true" />
      </button>
      <span className={available ? "ok" : "warn"}>{available ? "Available" : "Away"}</span>
      {deferred > 0 && <span className="badge warnb" title="Jobs waiting for you at the end of the queue">{deferred} waiting</span>}
      {error && <span className="err">{error}</span>}
    </span>
  );
}
