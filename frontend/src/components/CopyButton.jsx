import { useState } from "react";

export default function CopyButton({ text }) {
  const [label, setLabel] = useState("Copy");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setLabel("Copied");
    } catch {
      setLabel("Select + copy");
    }
    setTimeout(() => setLabel("Copy"), 1500);
  }
  return (
    <button type="button" className="act sec sm" onClick={copy}>
      {label}
    </button>
  );
}
