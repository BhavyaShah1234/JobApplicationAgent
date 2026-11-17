// Small, pure helpers for showing times and states. The agent's timestamps are UTC ("2026-11-17T09:01:42Z").

export function parse(t) {
  if (!t) return null;
  const d = new Date(String(t).replace(" ", "T").replace(/(\+00:00)?Z?$/, "Z"));
  return Number.isNaN(d.getTime()) ? null : d;
}

export function ago(t, now = Date.now()) {
  const d = parse(t);
  if (!d) return "";
  const s = Math.max(0, (now - d.getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function dur(s) {
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

// The colour family of a status word.
export function kind(text) {
  const s = String(text || "");
  if (/fail|block|error|reject|stalled/.test(s)) return "bad";
  if (/review|warn|waiting|unknown|pending|needs/.test(s)) return "warn";
  if (/running/.test(s)) return "run";
  if (/submit|confirm|ok|worked|filled|done|finished|applied|queued|scraped|interview|offer/.test(s)) return "ok";
  return "";
}

export const STATE_TEXT = { running: "running", done: "finished", failed: "failed", blocked: "stopped", stalled: "stalled", waiting: "waiting" };
export const ICON = { done: "✓", running: "●", pending: "○", failed: "✕", blocked: "⏸", stalled: "!", skipped: "–" };

// "step 2 of 3" style text for a stage that iterates.
export function iteration(s) {
  if (s.i != null && s.n) return `${s.i} of ${s.n}`;
  if (s.i != null) return `${s.i}`;
  return "";
}
