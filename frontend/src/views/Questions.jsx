import { useState } from "react";
import { post } from "../api.js";
import { ago } from "../format.js";
import { usePoll } from "../hooks.js";
import Cmd from "../components/Cmd.jsx";
import Empty from "../components/Empty.jsx";

function Question({ q, readOnly, onDone }) {
  const [text, setText] = useState("");             // lives in this component: a refresh of the data never touches it
  const [msg, setMsg] = useState("");
  const terminal = q.secret || readOnly;
  async function send() {
    const r = await post("/api/hitl/answer", { id: q.id, value: text });
    setMsg(r.ok ? "Sent." : r.body.error || "failed");
    if (r.ok) setTimeout(onDone, 600);
  }
  return (
    <div className="box">
      <div>
        <b>Question {q.id}</b> <span className="mut">{ago(q.created)}</span>
      </div>
      <p>{q.prompt}</p>
      {terminal ? (
        <div>
          <p className="warn">{q.secret ? "This asks for a secret. Answer it in a terminal, never here:" : "Answer it in a terminal:"}</p>
          <Cmd text={q.terminal_command} />
        </div>
      ) : (
        <div>
          <textarea rows={2} value={text} placeholder={q.default ? `default: ${q.default}` : "your answer"} onChange={(e) => setText(e.target.value)} />
          <div style={{ marginTop: 8 }}>
            <button type="button" className="act" onClick={send}>
              Send answer
            </button>{" "}
            <span className="mut">{msg}</span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Questions({ config, onChange }) {
  const { data, reload } = usePoll("/api/hitl", 5000);
  if (!data) return <p className="mut">Loading…</p>;
  if (!data.length) return <Empty>The agent is not waiting on anything.</Empty>;
  return (
    <div>
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
