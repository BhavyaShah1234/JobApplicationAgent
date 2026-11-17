// A titled box. Inside a `.grid2`/`.grid3` row every panel stretches to the row's height, so a row always lines up.
export default function Panel({ title, note, children }) {
  return (
    <section className="panel">
      <h3>{title}</h3>
      <div className="pbody">{children}</div>
      {note && <p className="pnote">{note}</p>}
    </section>
  );
}
