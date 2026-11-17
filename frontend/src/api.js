// The only place that talks to the dashboard server. Every request carries the session cookie; a 401 means the page was opened
// without the access link.

export class SignedOut extends Error {}

export async function get(path) {
  const r = await fetch(path, { credentials: "same-origin" });
  if (r.status === 401) throw new SignedOut("signed out");
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Dashboard": "1" },
    body: JSON.stringify(body),
  });
  let data = {};
  try {
    data = await r.json();
  } catch {
    /* an empty or non-JSON body */
  }
  return { ok: r.ok, body: data };
}
