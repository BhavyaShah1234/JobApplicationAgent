import { TipBody, useTip } from "../tooltip.jsx";

// A headline number. `hint` (what it counts and how) appears on hover, focus or tap.
export default function Stat({ n, label, sub, hot, hint }) {
  const tip = useTip();
  return (
    <div className={`card${hot && n ? " hot" : ""}${hint ? " hov" : ""}`} {...(hint ? tip(<TipBody title={label} note={hint} />, `${label}: ${n}. ${hint}`) : {})}>
      <b>{n}</b>
      <span>{label}</span>
      {sub ? <div className="csub">{sub}</div> : null}
    </div>
  );
}
