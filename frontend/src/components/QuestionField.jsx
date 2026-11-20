import { useId } from "react";

const KIND_LABEL = {
  text: "Short text", textarea: "Long text", email: "Email address", tel: "Phone number", url: "Web address", number: "Number", date: "Date",
  select: "Dropdown: pick one", combobox: "Search list: pick one (or type)", radio: "Pick one", checkboxes: "Tick any that apply",
  checkbox: "Yes / No box", file: "File upload", other: "Other",
};

// The kind of answer asked for, in words ("Dropdown: pick one"), so it is clear before reading the control.
export function kindLabel(q) {
  return q.kind === "select" && q.multiple ? "Dropdown: pick several" : KIND_LABEL[q.kind] || KIND_LABEL.other;
}

const INPUT_TYPE = { email: "email", tel: "tel", url: "url", number: "number" };

// One question and its control. `value` is a string, an array (several choices) or a boolean (a single box); `onChange` gets the new one.
export default function QuestionField({ q, value, onChange, disabled, inBatch = false }) {
  const id = useId();
  const options = q.options || [];
  const set = (v) => onChange(v);
  let control;
  if (q.kind === "select" && !q.multiple) {
    control = (
      <select id={id} value={value ?? ""} disabled={disabled} onChange={(e) => set(e.target.value)}>
        <option value="">Choose one… ({options.length} options)</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  } else if (q.kind === "combobox") {
    control = (
      <>
        <input id={id} list={id + "-list"} value={value ?? ""} disabled={disabled} placeholder="Type to search, or pick" onChange={(e) => set(e.target.value)} />
        <datalist id={id + "-list"}>{options.map((o) => <option key={o} value={o} />)}</datalist>
        {!q.options_complete && <div className="mut qhelp">The list shows the options the agent could see; the form may offer more as you type.</div>}
      </>
    );
  } else if (q.kind === "radio") {
    control = (
      <div role="radiogroup" aria-labelledby={id + "-l"} className="choices">
        {options.map((o) => (
          <label key={o} className="choice"><input type="radio" name={id} checked={value === o} disabled={disabled} onChange={() => set(o)} /> {o}</label>
        ))}
        {value ? <button type="button" className="act sec sm" disabled={disabled} onClick={() => set("")}>Clear</button> : null}
      </div>
    );
  } else if (q.kind === "checkboxes" || (q.kind === "select" && q.multiple)) {
    const chosen = Array.isArray(value) ? value : [];
    control = (
      <div role="group" aria-labelledby={id + "-l"} className="choices">
        {options.map((o) => (
          <label key={o} className="choice">
            <input type="checkbox" checked={chosen.includes(o)} disabled={disabled}
              onChange={(e) => set(e.target.checked ? [...chosen, o] : chosen.filter((x) => x !== o))} /> {o}
          </label>
        ))}
      </div>
    );
  } else if (q.kind === "checkbox") {
    control = (
      <label className="choice"><input type="checkbox" checked={value === true} disabled={disabled} onChange={(e) => set(e.target.checked)} /> Yes (tick for yes, leave empty for no)</label>
    );
  } else if (q.kind === "file") {
    control = <p className="mut">A file is attached by the agent from your saved documents. If it asks you for one, add it in the Browser tab.</p>;
  } else if (q.kind === "textarea") {
    control = <textarea id={id} rows={4} value={value ?? ""} disabled={disabled} placeholder="your answer" onChange={(e) => set(e.target.value)} />;
  } else {
    control = <input id={id} type={INPUT_TYPE[q.kind] || "text"} value={value ?? ""} disabled={disabled} placeholder="your answer" onChange={(e) => set(e.target.value)} />;
  }
  return (
    <div className="qfield">
      <div className="qhead" id={id + "-l"}>
        <label htmlFor={id}><b>{q.label}</b></label>
        {q.required && <span className="chip bad">required</span>}
        <span className="chip">{kindLabel(q)}</span>
        {q.section && <span className="mut">{q.section}</span>}
      </div>
      {q.help && <div className="mut qhelp">{q.help}</div>}
      {q.kind === "radio" && (q.options || []).length === 1 && q.options[0].trim().toLowerCase() === (q.label || "").trim().toLowerCase() && (
        <div className="warn qhelp">The agent could not read the question this choice belongs to (the text sits outside the form control). Open the Browser tab to see the page before answering.</div>
      )}
      {control}
      {(q.quick || []).filter((w) => !(inBatch && w === "skip")).length > 0 && (
        <div className="quick">
          {q.quick.filter((w) => !(inBatch && w === "skip")).map((w) => <button key={w} type="button" className="act sec sm" disabled={disabled} onClick={() => set(w)}>{w[0].toUpperCase() + w.slice(1)}</button>)}
        </div>
      )}
    </div>
  );
}
