// A table whose cells are given by `render(row)` (an array). Agent data is always rendered as React text, never as HTML.
export default function DataTable({ cols, rows, render, rowKey }) {
  return (
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
  );
}
