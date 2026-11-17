import { usePoll } from "../hooks.js";
import Chip from "../components/Chip.jsx";
import Cmd from "../components/Cmd.jsx";
import DataTable from "../components/DataTable.jsx";
import Empty from "../components/Empty.jsx";

export default function Review() {
  const { data } = usePoll("/api/review", 8000);
  return (
    <div>
      <p className="mut">Nothing is sent from this page. Sending an application needs your typed approval in a terminal, per application.</p>
      {!data ? (
        <p className="mut">Loading…</p>
      ) : !data.length ? (
        <Empty>No application is waiting for your review.</Empty>
      ) : (
        <DataTable
          cols={["#", "Company", "Title", "Ready", "Waiting", "Blockers", "Open its review (copy into a terminal)"]}
          rows={data}
          rowKey={(x) => x.id}
          render={(x) => [
            x.id,
            x.company,
            x.title,
            <Chip key="r" tone={x.ready ? "ok" : "warn"}>{x.ready ? "ready" : "not ready"}</Chip>,
            x.age_hours != null ? `${x.age_hours} h` : "-",
            <div key="b">{x.blockers.length ? x.blockers.slice(0, 3).map((b, i) => <div key={i} className="warn">{b}</div>) : "-"}</div>,
            <Cmd key="c" text={x.review_command} />,
          ]}
        />
      )}
    </div>
  );
}
