import { kind } from "../format.js";

// A small coloured pill. `tone` forces the colour; otherwise it follows the word.
export default function Chip({ tone, children }) {
  const t = tone ?? kind(typeof children === "string" ? children : "");
  return <span className={`chip ${t}`.trim()}>{children}</span>;
}
