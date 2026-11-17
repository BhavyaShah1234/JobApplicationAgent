import { vi } from "vitest";

// Replace fetch with a table of path -> JSON (or a function of the request). Returns the mock so a test can look at the calls.
export function mockFetch(routes) {
  const fn = vi.fn(async (path, init = {}) => {
    const hit = routes[String(path).split("?")[0]];
    if (hit === undefined) return { ok: false, status: 404, json: async () => ({ error: "not found" }) };
    const out = typeof hit === "function" ? await hit(init) : hit;
    if (out && out.__status) return { ok: out.__status < 400, status: out.__status, json: async () => out.body ?? {} };
    return { ok: true, status: 200, json: async () => out };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export const JOB = {
  job: "101", url: "https://jobs.example.test/1", title: "Robotics Engineer", company: "Example Robotics", state: "running", active: true, percent: 67,
  current: "documents", elapsed_s: 840, updated: "2026-11-17T09:00:00Z", started: "2026-11-17T08:46:00Z",
  stages: [
    { id: "scrape", label: "Read posting", status: "done", step: "", i: null, n: null, detail: "" },
    { id: "research", label: "Research company", status: "done", step: "", i: null, n: null, detail: "" },
    { id: "documents", label: "Resume & cover letter", status: "running", step: "ATS round", i: 2, n: 3, detail: "scoring on the checker sites" },
    { id: "apply", label: "Fill the form", status: "pending", step: "", i: null, n: null, detail: "" },
  ],
  trail: [{ ts: "2026-11-17T08:59:00Z", stage: "documents", status: "running", label: "ATS round", i: 2, n: 3, detail: "scoring" }],
};
