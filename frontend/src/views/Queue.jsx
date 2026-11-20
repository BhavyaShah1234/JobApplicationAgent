import { useMemo, useRef, useState } from "react";
import { post } from "../api.js";
import { ago } from "../format.js";
import { usePoll } from "../hooks.js";
import Chip from "../components/Chip.jsx";
import Empty from "../components/Empty.jsx";
import FetchError from "../components/FetchError.jsx";

const FILTERS = [["queued", "Queued"], ["running", "Running"], ["needs_review", "Needs review"], ["skipped", "Set aside"], ["failed", "Failed"], ["all", "All"]];
const WHERE = [["normal", "By match score"], ["top", "Next to run"], ["bottom", "Last"]];

async function act(path, body, setMsg, reload) {
  try {
    const r = await post(path, body);
    if (!r.ok) setMsg({ text: r.body.error || "failed", ok: false });
    else reload?.();
    return r;
  } catch {
    setMsg({ text: "Could not reach the dashboard.", ok: false });
    return { ok: false, body: {} };
  }
}

function AddBox({ readOnly, reload }) {
  const [text, setText] = useState("");
  const [where, setWhere] = useState("normal");
  const [results, setResults] = useState([]);
  const [msg, setMsg] = useState({ text: "", ok: true });
  const [busy, setBusy] = useState(false);
  async function add() {
    if (busy) return;
    setBusy(true);
    const r = await act("/api/queue/add", { text, where }, setMsg, reload);
    if (r.ok) { setResults(r.body.results || []); setText(""); setMsg({ text: "", ok: true }); }
    setBusy(false);
  }
  if (readOnly) return null;
  return (
    <div className="box">
      <b>Add job links or companies</b>
      <p className="mut qhelp">One per line. A job link goes into the queue; a company name goes on the company list below, and Run now finds its openings. A link you add stays in the queue until you change it.</p>
      <textarea rows={3} value={text} placeholder={"https://job-boards.greenhouse.io/acme/jobs/123\nAcme Robotics"} onChange={(e) => setText(e.target.value)} />
      <div className="tools" style={{ marginTop: 8 }}>
        <label>Where in the queue <select value={where} onChange={(e) => setWhere(e.target.value)}>{WHERE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <button type="button" className="act" onClick={add} disabled={busy || !text.trim()}>{busy ? "Adding…" : "Add"}</button>
        <span className={msg.ok ? "mut" : "bad"}>{msg.text}</span>
      </div>
      {results.length > 0 && (
        <ul className="results">
          {results.map((r, i) => <li key={i}><Chip tone={["added", "requeued", "moved"].includes(r.result) ? "ok" : r.result === "exists" || r.result === "already_queued" ? "run" : "warn"}>{r.result.replace("_", " ")}</Chip> <span>{r.input}</span> <span className="mut">{r.note}</span></li>)}
        </ul>
      )}
    </div>
  );
}

function Companies({ readOnly, reload: reloadQueue }) {
  const { data, error, reload } = usePoll("/api/watchlist", 10000);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState({ text: "", ok: true });
  const both = () => { reload(); reloadQueue?.(); };
  async function addCompany() {
    const r = await act("/api/watchlist/add", { company: name }, setMsg, both);
    if (r.ok) { setName(""); setMsg({ text: r.body.note || "", ok: true }); }
  }
  async function run(c) {
    const r = await act("/api/watchlist/run", { companies: [c.company], limit: c.max_jobs }, setMsg, both);
    if (r.ok) setMsg({ text: `Started a run for ${c.company}. ${r.body.note}`, ok: true });
  }
  return (
    <div>
      <h2>Companies</h2>
      <FetchError error={error} reload={reload} />
      {!readOnly && (
        <div className="tools">
          <input value={name} placeholder="Add a company, e.g. Zoox" onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && addCompany()} />
          <button type="button" className="act" disabled={!name.trim()} onClick={addCompany}>Add company</button>
          <span className={msg.ok ? "mut" : "bad"}>{msg.text}</span>
        </div>
      )}
      {data && !data.length ? <Empty>No companies yet. Add one to have the agent look for its openings.</Empty> : (
        <div className="tscroll"><table>
          <thead><tr><th>Company</th><th>Postings</th><th>Queued</th><th>Jobs per run</th><th>Last run</th><th /></tr></thead>
          <tbody>
            {(data || []).map((c) => (
              <tr key={c.id} className={c.enabled ? "" : "off"}>
                <td>{c.company}{!c.enabled && <> <Chip tone="warn">off</Chip></>}</td>
                <td>{c.postings}</td><td>{c.queued}</td>
                <td>{readOnly ? c.max_jobs : <input type="number" min="1" max="20" style={{ width: 64 }} defaultValue={c.max_jobs} aria-label={`Jobs per run for ${c.company}`}
                  onBlur={(e) => Number(e.target.value) !== c.max_jobs && act("/api/watchlist/update", { id: c.id, max_jobs: Number(e.target.value) }, setMsg, both)} />}</td>
                <td className="mut">{c.last_run_at ? ago(c.last_run_at) : "never"}</td>
                <td>{!readOnly && <span className="rowact">
                  <button type="button" className="act sm" disabled={!c.enabled} onClick={() => run(c)}>Run now</button>
                  <button type="button" className="act sec sm" onClick={() => act("/api/watchlist/update", { id: c.id, enabled: !c.enabled }, setMsg, both)}>{c.enabled ? "Turn off" : "Turn on"}</button>
                  <button type="button" className="act sec sm" onClick={() => act("/api/watchlist/update", { id: c.id, remove: true }, setMsg, both)}>Remove</button>
                </span>}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}

function EditRow({ x, onSave, onCancel }) {
  const [f, setF] = useState({ company: x.company || "", title: x.title || "", location: x.location || "", notes: x.notes || "" });
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));
  return (
    <td colSpan={8}>
      <div className="editgrid">
        <label>Company<input value={f.company} onChange={set("company")} /></label>
        <label>Title<input value={f.title} onChange={set("title")} /></label>
        <label>Location<input value={f.location} onChange={set("location")} /></label>
        <label>Note<input value={f.notes} onChange={set("notes")} /></label>
      </div>
      <button type="button" className="act sm" onClick={() => onSave(f)} disabled={!f.company.trim()}>Save</button>{" "}
      <button type="button" className="act sec sm" onClick={onCancel}>Cancel</button>
    </td>
  );
}

function RunBox({ queued, readOnly }) {
  const [through, setThrough] = useState("apply");
  const [limit, setLimit] = useState(Math.min(5, Math.max(queued, 1)));
  const [noAts, setNoAts] = useState(false);
  const [msg, setMsg] = useState({ text: "", ok: true });
  if (readOnly) return null;
  async function run() {
    const r = await act("/api/queue/run", { through, limit: Number(limit), no_ats: noAts }, setMsg);
    setMsg(r.ok ? { text: `Started (pid ${r.body.pid}). It takes the queue top-down and stops at each review screen.`, ok: true } : { text: r.body.error || "failed", ok: false });
  }
  return (
    <div className="box">
      <b>Run the queue</b>
      <div className="tools" style={{ marginTop: 8 }}>
        <label>Take the first <input type="number" min="1" max="20" style={{ width: 64 }} value={limit} onChange={(e) => setLimit(e.target.value)} /> queued jobs</label>
        <label>as far as <select value={through} onChange={(e) => setThrough(e.target.value)}>{["scrape", "tailor", "apply"].map((v) => <option key={v}>{v}</option>)}</select></label>
        <label><input type="checkbox" checked={noAts} onChange={(e) => setNoAts(e.target.checked)} /> Skip optional ATS scans</label>
        <button type="button" className="act" disabled={!queued} onClick={run}>Run queue</button>
        <span className={msg.ok ? "mut" : "bad"}>{msg.text}</span>
      </div>
      <p className="mut qhelp">{queued} queued. A run never submits: every application waits for your approval in a terminal.</p>
    </div>
  );
}

export default function Queue({ config = {} }) {
  const readOnly = !!config.read_only;
  const [filter, setFilter] = useState("queued");
  const [q, setQ] = useState("");
  const [dragging, setDragging] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [editing, setEditing] = useState(null);
  const [msg, setMsg] = useState({ text: "", ok: true });
  const dragOver = useRef(null);
  const { data, error, reload } = usePoll("/api/queue", dragging ? 0 : 8000);
  const rows = useMemo(() => {
    const needle = q.toLowerCase();
    return (data || []).filter((x) => (filter === "all" || x.status === filter) && (!needle || `${x.company} ${x.title} ${x.status} ${x.role_id} ${x.notes}`.toLowerCase().includes(needle)));
  }, [data, filter, q]);
  if (!data) return <><FetchError error={error} reload={reload} /><p className="mut">Loading…</p></>;
  const counts = Object.fromEntries(FILTERS.map(([k]) => [k, k === "all" ? data.length : data.filter((x) => x.status === k).length]));
  const queued = data.filter((x) => x.status === "queued");
  const reorderable = !readOnly && filter === "queued" && !q;
  const toggle = (id) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const ids = [...selected];
  const bulk = async (path, body, done) => { const r = await act(path, body, setMsg, reload); if (r.ok) { setSelected(new Set()); setMsg({ text: done(r.body), ok: true }); } };
  async function drop(targetId) {
    const from = dragging;
    setDragging(null);
    if (from == null || from === targetId) return;
    const order = queued.map((x) => x.id).filter((i) => i !== from);
    order.splice(order.indexOf(targetId), 0, from);
    await act("/api/queue/reorder", { ids: order }, setMsg, reload);
  }
  return (
    <div>
      <FetchError error={error} reload={reload} />
      <AddBox readOnly={readOnly} reload={reload} />
      <RunBox queued={queued.length} readOnly={readOnly} />
      <Companies readOnly={readOnly} reload={reload} />
      <h2>Queue</h2>
      <div className="tools">
        <div role="tablist" className="seg-tabs">
          {FILTERS.map(([k, l]) => <button key={k} type="button" role="tab" aria-selected={filter === k} className={`act sm ${filter === k ? "" : "sec"}`} onClick={() => { setFilter(k); setSelected(new Set()); }}>{l} <span className="mut">{counts[k]}</span></button>)}
        </div>
        <input type="search" placeholder="Filter by company, title or note" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="mut">{rows.length} shown</span>
      </div>
      {reorderable && queued.length > 1 && <p className="mut qhelp">Drag a row, or use the arrows, to change the order the agent takes them. The top row runs next.</p>}
      {!readOnly && ids.length > 0 && (
        <div className="tools bulk">
          <b>{ids.length} selected</b>
          <button type="button" className="act sm" onClick={() => bulk("/api/queue/state", { ids, action: "queue" }, (b) => `Put ${b.changed} back in the queue${b.refused?.length ? `; ${b.refused.length} could not be` : ""}.`)}>Put in queue</button>
          <button type="button" className="act sec sm" onClick={() => bulk("/api/queue/state", { ids, action: "skip" }, (b) => `Set ${b.changed} aside${b.refused?.length ? `; ${b.refused.length} could not be` : ""}.`)}>Set aside</button>
          <button type="button" className="act sec sm" onClick={() => window.confirm(`Delete ${ids.length} posting(s) from the list? A posting with an application is kept.`) && bulk("/api/queue/delete", { ids }, (b) => `Deleted ${b.deleted}${b.refused?.length ? `; ${b.refused.length} kept` : ""}.`)}>Delete</button>
        </div>
      )}
      <span className={msg.ok ? "mut" : "bad"}>{msg.text}</span>
      {!rows.length ? <Empty>Nothing here{filter === "queued" ? ": add a job link or a company above." : "."}</Empty> : (
        <div className="tscroll"><table className="qtable" aria-label="Queue">
          <thead><tr>{!readOnly && <th />}<th>#</th><th>Company</th><th>Title</th><th>Status</th><th>Role</th><th>Score</th><th>Updated</th>{!readOnly && <th />}</tr></thead>
          <tbody>
            {rows.map((x) => editing === x.id ? (
              <tr key={x.id}><EditRow x={x} onCancel={() => setEditing(null)} onSave={async (f) => { const r = await act("/api/queue/edit", { id: x.id, ...f }, setMsg, reload); if (r.ok) setEditing(null); }} /></tr>
            ) : (
              <tr key={x.id} draggable={reorderable && x.status === "queued"} className={dragging === x.id ? "dragging" : ""}
                onDragStart={() => setDragging(x.id)} onDragEnd={() => setDragging(null)}
                onDragOver={(e) => { if (dragging != null) { e.preventDefault(); dragOver.current = x.id; } }} onDrop={(e) => { e.preventDefault(); drop(x.id); }}>
                {!readOnly && <td><input type="checkbox" aria-label={`Select ${x.company} ${x.title}`} checked={selected.has(x.id)} disabled={!x.editable} onChange={() => toggle(x.id)} /></td>}
                <td>{x.position ?? ""}</td>
                <td>{x.company}{x.user_edited && <> <span className="mut" title="You changed this; discovery will not overwrite it">✎</span></>}</td>
                <td>{x.url ? <a href={x.url} target="_blank" rel="noreferrer noopener">{x.title || "(title appears after the scrape)"}</a> : x.title}{x.notes && <div className="mut qhelp">{x.notes}</div>}</td>
                <td><Chip>{x.status}</Chip></td><td>{x.role_id || ""}</td><td>{x.match_score ?? ""}</td><td className="mut">{ago(x.updated_at)}</td>
                {!readOnly && <td><span className="rowact">
                  {reorderable && x.status === "queued" && <>
                    <button type="button" className="act sec sm" aria-label={`Move ${x.company} to the top`} title="Top" onClick={() => act("/api/queue/move", { id: x.id, to: "top" }, setMsg, reload)}>⤒</button>
                    <button type="button" className="act sec sm" aria-label={`Move ${x.company} up`} title="Up" onClick={() => act("/api/queue/move", { id: x.id, to: "up" }, setMsg, reload)}>↑</button>
                    <button type="button" className="act sec sm" aria-label={`Move ${x.company} down`} title="Down" onClick={() => act("/api/queue/move", { id: x.id, to: "down" }, setMsg, reload)}>↓</button>
                    <button type="button" className="act sec sm" aria-label={`Move ${x.company} to the bottom`} title="Bottom" onClick={() => act("/api/queue/move", { id: x.id, to: "bottom" }, setMsg, reload)}>⤓</button>
                  </>}
                  {x.editable && <button type="button" className="act sec sm" onClick={() => setEditing(x.id)}>Edit</button>}
                </span></td>}
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}
