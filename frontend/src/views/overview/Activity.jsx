import Panel from "../../components/Panel.jsx";
import TimelineChart from "../../charts/TimelineChart.jsx";

export default function ActivitySection({ a }) {
  return (
    <>
      <h2>Activity, last {a.timeline.length} days</h2>
      <div className="grid2">
        <Panel title="Your applications" note="Filled to review, submitted, and replies received, per day.">
          <TimelineChart days={a.timeline} show={["filled", "submitted", "responses"]} />
        </Panel>
        <Panel title="Postings found" note="What discovery brought in each day, before any filtering.">
          <TimelineChart days={a.timeline} show={["found"]} />
        </Panel>
      </div>
    </>
  );
}
