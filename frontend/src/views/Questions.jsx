import { useState } from "react";
import { post } from "../api.js";
import { ago } from "../format.js";
import { usePoll } from "../hooks.js";
import Cmd from "../components/Cmd.jsx";
import Empty from "../components/Empty.jsx";
import FetchError from "../components/FetchError.jsx";
import QuestionField from "../components/QuestionField.jsx";

// Who and what the question is about: company, role, step. All from the run's own record.
function About({ q }) {
  const c = q.context || {};
  const bits = [c.company, c.title, c.stage && `step: ${c.stage}`].filter(Boolean);
  return bits.length ? <div className="qabout">{bits.map((b, i) => <span key={i} className="chip run">{b}</span>)}</div> : null;
}

// A request with structure: one control per question, the right kind, every option shown, then one Send.
function Structured({ q, onDone }) {
  const [values, setValues] = useState(() => Object.fromEntries(q.questions.map((x) => [x.id, x.kind === "checkboxes" || x.multiple ? [] : x.kind === "checkbox" ? false : ""])));
  const [rule, setRule] = useState("");
  const [teach, setTeach] = useState(false);
  const [msg, setMsg] = useState({ text: "", ok: true });
  const [busy, setBusy] = useState(false);
  const single = q.questions.length === 1;
  const answered = q.questions.filter((x) => { const v = values[x.id]; return Array.isArray(v) ? v.length : v === true || (typeof v === "string" && v.trim()); }).length;
  const missing = q.questions.filter((x) => x.required && x.kind !== "file" && !(Array.isArray(values[x.id]) ? values[x.id].length : values[x.id] === true || String(values[x.id] ?? "").trim()));
  async function send(overrides) {
    if (busy) return;
    setBusy(true);
    try {
      const answers = { ...values, ...overrides };
      const body = { id: q.id, answers };
      if (teach && rule.trim() && single) body.rule = rule.trim();
      const r = await post("/api/hitl/answer", body);
      setMsg(r.ok ? { text: "Sent.", ok: true } : { text: r.body.error || "failed", ok: false });
      if (r.ok) setTimeout(onDone, 600);
    } catch {
      setMsg({ text: "Could not send the answer. Check the connection before retrying.", ok: false });
    } finally { setBusy(false); }
  }
  return (
    <div>
      {q.questions.map((x) => (
        <QuestionField key={x.id} q={x} value={values[x.id]} disabled={busy} inBatch={!single}
          onChange={(v) => { if ((x.quick || []).includes(v) && single && typeof v === "string") send({ [x.id]: v }); else setValues((s) => ({ ...s, [x.id]: v })); }} />
      ))}
      {single && !q.questions[0].options?.length && q.reply_format === "text" && (
        <p>
          <label><input type="checkbox" checked={teach} onChange={(e) => setTeach(e.target.checked)} /> Teach a rule instead: how to answer questions like this for every company</label>
          {teach && <textarea rows={2} value={rule} placeholder="e.g. always answer No" onChange={(e) => setRule(e.target.value)} />}
        </p>
      )}
      <div style={{ marginTop: 8 }}>
        <button type="button" className="act" onClick={() => send()} disabled={busy}>
          {single ? "Send answer" : `Send ${answered} of ${q.questions.length} answers`}
        </button>{" "}
        <button type="button" className="act sec" onClick={() => send(Object.fromEntries(q.questions.map((x) => [x.id, ""])))} disabled={busy}>Skip {single ? "this question" : "all"}</button>{" "}
        <span className={msg.ok ? "mut" : "bad"}>{msg.text}</span>
      </div>
      {missing.length > 0 && <p className="warn qhelp">Required and still empty: {missing.map((x) => x.label.length > 60 ? x.label.slice(0, 57) + "…" : x.label).join("; ")}. Unanswered questions are left for the review screen.</p>}
    </div>
  );
}

// A plain prompt (no structure): a text box, plus a button for any one-word reply the prompt quotes ("answer 'done'").
function Plain({ q, onDone }) {
  const [text, setText] = useState("");                  // lives in this component: a refresh of the data never touches it
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  async function send(value = text) {
    if (busy) return;
    setBusy(true);
    try {
      const r = await post("/api/hitl/answer", { id: q.id, value });
      setMsg(r.ok ? "Sent." : r.body.error || "failed");
      if (r.ok) setTimeout(onDone, 600);
    } catch { setMsg("Could not send the answer. Check the connection before retrying."); }
    finally { setBusy(false); }
  }
  const quick = (q.questions?.[0]?.quick) || [];
  return (
    <div>
      <textarea rows={2} value={text} placeholder={q.default ? `default: ${q.default}` : "your answer"} onChange={(e) => setText(e.target.value)} />
      <div style={{ marginTop: 8 }}>
        <button type="button" className="act" onClick={() => send()} disabled={busy}>Send answer</button>{" "}
        {quick.map((w) => <button key={w} type="button" className="act sec" onClick={() => send(w)} disabled={busy}>{w[0].toUpperCase() + w.slice(1)}</button>)}{" "}
        <span className="mut">{msg}</span>
      </div>
    </div>
  );
}

function Question({ q, readOnly, onDone }) {
  const terminal = q.secret || q.terminal_only || readOnly;
  return (
    <div className="box">
      <div>
        <b>Question {q.id}</b> <span className="mut">{ago(q.created)}</span>
      </div>
      <About q={q} />
      {!q.structured && <p>{q.prompt}</p>}
      {q.structured && q.questions.length > 1 && <p className="mut">{q.questions.length} questions from one form page.</p>}
      {terminal ? (
        <div>
          {q.structured && q.questions.map((x) => <QuestionField key={x.id} q={x} value="" onChange={() => {}} disabled />)}
          <p className="warn">{q.secret ? "This asks for a secret. Answer it in a terminal, never here:" : q.terminal_only ? "This decision requires a terminal:" : "Answer it in a terminal:"}</p>
          <Cmd text={q.terminal_command} />
        </div>
      ) : q.structured ? <Structured q={q} onDone={onDone} /> : <Plain q={q} onDone={onDone} />}
    </div>
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
    </div>
  );
}
