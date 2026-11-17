import FunnelChart from "../../charts/FunnelChart.jsx";
import Panel from "../../components/Panel.jsx";

export default function FunnelSection({ a }) {
  return (
    <>
      <h2>From posting to answer</h2>
      <Panel title="Funnel" note="Each bar is a count; the percentage is the share kept from the step above.">
        <FunnelChart steps={a.funnel} />
      </Panel>
    </>
  );
}
