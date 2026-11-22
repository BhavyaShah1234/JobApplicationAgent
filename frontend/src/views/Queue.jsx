import { useEffect, useMemo, useState } from "react";
import { post } from "../api.js";
import { ago } from "../format.js";
import { usePoll } from "../hooks.js";
import Chip from "../components/Chip.jsx";
import DataTable from "../components/DataTable.jsx";
import FetchError from "../components/FetchError.jsx";
import RunForm from "../components/RunForm.jsx";

// The statuses a posting can be deleted from: nobody is working on it and nothing was sent. The server enforces this too.
const DELETABLE = new Set(["queued", "needs_review", "skipped", "failed", "blocked"]);
const ORDER = ["queued", "needs_review", "running", "scraped", "applied", "already_applied", "skipped", "failed", "blocked", "closed"];
const canDelete = (x) => DELETABLE.has(x.status) && !x.has_application;
const safeLink = (u) => (/^https?:\/\//i.test(u || "") ? u : null);

export default function Queue({ config = {}, onOpenJobs }) {
  const readOnly = !!config.read_only;
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [picked, setPicked] = useState(() => new Set());
  const [confirming, setConfirming] = useState(null);                 // ids waiting for "Delete" to be confirmed
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState({ text: "", ok: true });
  const [started, setStarted] = useState(false);
  const { data, error, reload } = usePoll("/api/queue", 10000);
  const ids = useMemo(() => new Set((data || []).map((x) => x.id)), [data]);
  useEffect(() => setPicked((p) => (p.size && [...p].some((i) => !ids.has(i)) ? new Set([...p].filter((i) => ids.has(i))) : p)), [ids]);
  if (!data) return <><FetchError error={error} reload={reload} /><p className="mut">Loading…</p></>;

  const counts = data.reduce((m, x) => ({ ...m, [x.status]: (m[x.status] || 0) + 1 }), {});
  const statuses = [...ORDER.filter((s) => counts[s]), ...Object.keys(counts).filter((s) => !ORDER.includes(s))];
  const needle = q.toLowerCase();
  const rows = data.filter((x) => (status === "all" || x.status === status) && (!needle || `${x.company} ${x.title} ${x.status} ${x.role_id} ${x.notes}`.toLowerCase().includes(needle)));
  const deletable = rows.filter(canDelete);
  const allPicked = deletable.length > 0 && deletable.every((x) => picked.has(x.id));

  const toggle = (id) => setPicked((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleAll = () => setPicked((p) => { const n = new Set(p); deletable.forEach((x) => (allPicked ? n.delete(x.id) : n.add(x.id))); return n; });

  async function remove() {
    if (busy || !confirming?.length) return;
    setBusy(true);
    try {
      const r = await post("/api/queue/delete", { ids: confirming });
      if (r.ok) {
        const refused = r.body.refused || [];
        setNote({ text: `Deleted ${r.body.deleted} posting${r.body.deleted === 1 ? "" : "s"}.${refused.length ? ` Kept ${refused.length}: ${refused.map((x) => x.why).join("; ")}.` : ""}`, ok: refused.length === 0 });
        setPicked(new Set());
      } else {
        setNote({ text: r.body.error || "Could not delete.", ok: false });
      }
      setConfirming(null);
      reload();
    } catch {
      setNote({ text: "Could not contact the dashboard. Reload before retrying.", ok: false });
    } finally {
      setBusy(false);
    }
  }

  const cols = [...(readOnly ? [] : [<input key="all" type="checkbox" aria-label="Select all deletable postings shown" checked={allPicked} disabled={!deletable.length} onChange={toggleAll} />]),
    "Company", "Title", "Status", "Role", "Score", "Updated", "Note", ...(readOnly ? [] : [""])];
  const cells = (x) => {
    const link = safeLink(x.url);
    return [
      ...(readOnly ? [] : [<input key="p" type="checkbox" aria-label={`Select ${x.company} ${x.title}`} checked={picked.has(x.id)} disabled={!canDelete(x)}
        title={canDelete(x) ? "" : x.has_application ? "It has an application on record" : "Only waiting postings can be deleted"} onChange={() => toggle(x.id)} />]),
      x.company, link ? <a key="t" href={link} target="_blank" rel="noopener noreferrer">{x.title}</a> : x.title, <Chip key="s">{x.status}</Chip>, x.role_id || "", x.match_score ?? "",
      ago(x.updated_at), <span key="n" className="mut">{x.notes || ""}</span>,
      ...(readOnly ? [] : [canDelete(x) ? <button key="d" type="button" className="act sm sec" aria-label={`Delete ${x.company} ${x.title}`} onClick={() => setConfirming([x.id])}>Delete</button> : ""]),
    ];
  };
  return (
    <div>
      <FetchError error={error} reload={reload} />
      {readOnly ? (
        <p className="mut">Read-only view: add postings and delete them from a terminal (<code>jobagent run</code>, <code>jobagent queue</code>).</p>
      ) : (
        <details className="box" open>
          <summary><strong>Add to the queue / start a run</strong></summary>
          <RunForm onStarted={() => { setStarted(true); setTimeout(reload, 2500); }} />
          {started && <p className="ok">The run is working in the background. New postings appear in the table below. <button type="button" className="act sm sec" onClick={() => onOpenJobs?.()}>Watch it in Jobs</button></p>}
        </details>
      )}
      <div className="tools">
        <input type="search" placeholder="Filter by company, title or status" value={q} onChange={(e) => setQ(e.target.value)} />
        <button type="button" className={`act sm${status === "all" ? "" : " sec"}`} aria-pressed={status === "all"} onClick={() => setStatus("all")}>All ({data.length})</button>
        {statuses.map((s) => (
          <button key={s} type="button" className={`act sm${status === s ? "" : " sec"}`} aria-pressed={status === s} onClick={() => setStatus(s)}>{s.replace(/_/g, " ")} ({counts[s]})</button>
        ))}
        <span className="mut">{rows.length} of {data.length} postings</span>
      </div>
      {!readOnly && (
        <div className="tools">
          <button type="button" className="act sm danger" disabled={!picked.size || busy} onClick={() => setConfirming([...picked])}>Delete selected ({picked.size})</button>
          {picked.size > 0 && <button type="button" className="act sm sec" onClick={() => setPicked(new Set())}>Clear selection</button>}
        </div>
      )}
      {confirming && (
        <div className="box confirm" role="alertdialog" aria-label="Confirm delete">
          <p>Delete {confirming.length} posting{confirming.length === 1 ? "" : "s"} from the queue? Discovery will not add {confirming.length === 1 ? "it" : "them"} back. Postings that are running or have an application are always kept.</p>
          <button type="button" className="act danger" disabled={busy} onClick={remove}>{busy ? "Deleting…" : "Delete"}</button>{" "}
          <button type="button" className="act sec" disabled={busy} onClick={() => setConfirming(null)}>Cancel</button>
        </div>
      )}
      {note.text && <p className={note.ok ? "ok" : "bad"} role="status">{note.text}</p>}
      <DataTable cols={cols} rows={rows} rowKey={(x) => x.id} render={cells} />
    </div>
  );
}
