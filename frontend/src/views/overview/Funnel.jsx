import FunnelChart from "../../charts/FunnelChart.jsx";
import Panel from "../../components/Panel.jsx";

export default function FunnelSection({ a }) {
  return (
    <>
      <h2>From posting to answer</h2>
      <Panel title="Recorded milestones" note="Counts may include direct applications. Receipts and responses arrive independently; these bars are not conversion rates between matching groups.">
        <FunnelChart steps={a.funnel} />
      </Panel>
    </>
  );
}
