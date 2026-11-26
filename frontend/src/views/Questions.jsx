import { useState } from "react";
import { post } from "../api.js";
import { ago } from "../format.js";
import { usePoll } from "../hooks.js";
import Cmd from "../components/Cmd.jsx";
import Empty from "../components/Empty.jsx";
import FetchError from "../components/FetchError.jsx";

function Question({ q, readOnly, onDone }) {
  const [text, setText] = useState("");             // lives in this component: a refresh of the data never touches it
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const terminal = q.secret || q.terminal_only || readOnly;
  async function send() {
    if (busy) return;
    setBusy(true);
    try {
    const r = await post("/api/hitl/answer", { id: q.id, value: text });
    setMsg(r.ok ? "Sent." : r.body.error || "failed");
    if (r.ok) setTimeout(onDone, 600);
    } catch { setMsg('Could not send the answer. Check the connection before retrying.'); }
    finally { setBusy(false); }
  }
  return (
    <div className="box">
      <div>
        <b>Question {q.id}</b> <span className="mut">{ago(q.created)}</span>
      </div>
      <p>{q.prompt}</p>
      {terminal ? (
        <div>
          <p className="warn">{q.secret ? "This asks for a secret. Answer it in a terminal, never here:" : q.terminal_only ? "This decision requires a terminal:" : "Answer it in a terminal:"}</p>
          <Cmd text={q.terminal_command} />
        </div>
      ) : (
        <div>
          <textarea rows={2} value={text} placeholder={q.default ? `default: ${q.default}` : "your answer"} onChange={(e) => setText(e.target.value)} />
          <div style={{ marginTop: 8 }}>
            <button type="button" className="act" onClick={send} disabled={busy}>
              Send answer
            </button>{" "}
            <span className="mut">{msg}</span>
          </div>
        </div>
      )}
    </div>
  );
}

// A question an application asked earlier (saved while the candidate was away, or left unanswered). Its answer is kept for that
// application, and the next time the application asks exactly this question the agent uses it without asking.
function SavedQuestion({ q, readOnly, onDone }) {
  const [text, setText] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  async function send() {
    if (busy || (q.required && !text.trim())) return;
    setBusy(true);
    try {
      const r = await post("/api/saved-questions/answer", { job: q.job, qid: q.qid, value: text });
      setMsg(r.ok ? "Saved for the next run." : r.body.error || "failed");
      if (r.ok) setTimeout(onDone, 600);
    } catch { setMsg("Could not save the answer. Check the connection before retrying."); }
    finally { setBusy(false); }
  }
  return (
    <div className="box">
      <div>
        <b>{q.label}</b>{" "}
        {q.required && <span className="chip warn">required</span>}{" "}
        <span className="mut">{q.section ? q.section + " · " : ""}asked {ago(q.last_asked || q.first_asked)}</span>
      </div>
      {q.help && <p className="mut">{q.help}</p>}
      {readOnly ? (
        <p className="mut">This dashboard is read-only: answer saved questions from a dashboard that is not.</p>
      ) : (
        <div style={{ marginTop: 8 }}>
          {q.options?.length ? (
            <select aria-label={`Answer: ${q.label}`} value={text} onChange={(e) => setText(e.target.value)}>
              <option value="">choose…</option>
              {q.options.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : (
            <textarea rows={2} aria-label={`Answer: ${q.label}`} value={text} placeholder="your answer" onChange={(e) => setText(e.target.value)} />
          )}
          <div style={{ marginTop: 8 }}>
            <button type="button" className="act" onClick={send} disabled={busy || (q.required && !text.trim())}>Save answer</button>{" "}
            <span className="mut">{msg}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function SavedQuestions({ readOnly }) {
  const { data, reload } = usePoll("/api/saved-questions", 10000);
  if (!data?.length) return null;                     // nothing saved (or the route is unavailable): say nothing
  const groups = [];
  for (const q of data) {
    let g = groups.find((x) => x.job === q.job);
    if (!g) groups.push((g = { job: q.job, company: q.company, title: q.title, items: [] }));
    g.items.push(q);
  }
  return (
    <section>
      <h2>Saved for later</h2>
      <p className="mut">Questions applications asked while you were away or that were left unanswered. An answer here is used the next
        time that application runs, without asking you.</p>
      {groups.map((g) => (
        <div key={g.job}>
          <h3>{g.company}{g.title ? ` · ${g.title}` : ""}</h3>
          {g.items.map((q) => <SavedQuestion key={q.qid} q={q} readOnly={readOnly} onDone={reload} />)}
        </div>
      ))}
    </section>
  );
}

export default function Questions({ config, onChange }) {
  const { data, error, reload } = usePoll("/api/hitl", 5000);
  if (!data) return <><FetchError error={error} reload={reload} /><p className="mut">Loading…</p></>;
  return (
    <div>
      <FetchError error={error} reload={reload} />
      {!data.length && <Empty>The agent is not waiting on anything.</Empty>}
      {data.map((q) => (
        <Question
          key={q.id}
          q={q}
          readOnly={config.read_only}
          onDone={() => {
            reload();
            onChange?.();
          }}
        />
      ))}
      <SavedQuestions readOnly={config.read_only} />
    </div>
  );
}
