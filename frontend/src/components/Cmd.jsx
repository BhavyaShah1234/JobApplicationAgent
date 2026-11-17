import CopyButton from "./CopyButton.jsx";

// A command you paste into a terminal, with a copy button.
export default function Cmd({ text }) {
  return (
    <div className="cmd">
      <code>{text}</code>
      <CopyButton text={text} />
    </div>
  );
}
