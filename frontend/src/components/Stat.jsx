export default function Stat({ n, label, sub, hot }) {
  return (
    <div className={`card${hot && n ? " hot" : ""}`}>
      <b>{n}</b>
      <span>{label}</span>
      {sub ? <div className="csub">{sub}</div> : null}
    </div>
  );
}
