import { useState } from "react";
import { post } from "../api.js";

// Starts `jobagent run` in the background: it goes as far as the choice below and stops at the review screen, so it cannot submit. Lives on the Queue page,
// because what a run finds and what it works on is the queue. `onStarted` is called once the server accepted the run.
export default function RunForm({ onStarted }) {
  const [inputs, setInputs] = useState("");
  const [through, setThrough] = useState("apply");
  const [limit, setLimit] = useState(5);
  const [noAts, setNoAts] = useState(false);
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ text: "", ok: true });
  async function start() {
    if (busy) return;
    setBusy(true);
    try {
      const r = await post("/api/run", { inputs: inputs.split("\n").map((s) => s.trim()).filter(Boolean), through, limit: Number(limit), browser_mode: "auto", no_ats: noAts, force });
      setMsg(r.ok ? { text: `Started (pid ${r.body.pid}). ${r.body.note || ""}`.trim(), ok: true } : { text: r.body.error || "failed", ok: false });
      if (r.ok) {
        setInputs("");
        onStarted?.();
      }
    } catch {
      setMsg({ text: "Could not contact the dashboard. Check Runs before retrying.", ok: false });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      <p className="mut">
        Add job links or companies and start <code>jobagent run</code> in the background. Postings it finds are added to the queue below. It goes as far as the choice below and stops at the review screen: it cannot submit.
      </p>
      <textarea rows={4} value={inputs} placeholder="One per line: job URLs, company names, or a CSV/XLSX file name" onChange={(e) => setInputs(e.target.value)} />
      <p>
        Go as far as{" "}
        <select value={through} onChange={(e) => setThrough(e.target.value)} aria-label="Go as far as">
          {["scrape", "tailor", "apply"].map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
        {"  ·  "}jobs per company input{" "}
        <input type="number" min="1" max="20" value={limit} style={{ width: 70 }} aria-label="Jobs per company input" onChange={(e) => setLimit(e.target.value)} />
      </p>
      <p><label><input type="checkbox" checked={noAts} onChange={(e) => setNoAts(e.target.checked)} /> Skip optional ATS scans</label></p>
      <p><label><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> Rebuild documents and refresh earlier stages</label></p>
      <button type="button" className="act" onClick={start} disabled={busy || !inputs.trim()}>
        {busy ? "Starting…" : "Start"}
      </button>
      <p className={msg.ok ? "ok" : "bad"} role="status">{msg.text}</p>
    </div>
  );
}
