// A table whose cells are given by `render(row)` (an array). Agent data is always rendered as React text, never as HTML. The table scrolls
// sideways inside its own box on a narrow screen, so the page itself never scrolls sideways.
export default function DataTable({ cols, rows, render, rowKey }) {
  return (
    <div className="tscroll">
      <table>
        <thead>
          <tr>
            {cols.map((c, i) => (
              <th key={i}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((r, n) => (
              <tr key={rowKey ? rowKey(r) : n}>
                {render(r).map((c, i) => (
                  <td key={i}>{c}</td>
                ))}
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={cols.length} className="mut">
                Nothing here.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
