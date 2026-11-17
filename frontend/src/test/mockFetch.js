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

export const ANALYTICS = {
  generated: "2026-11-17T10:00:00Z",
  totals: { jobs: 3105, applications: 6, submitted: 4 },
  funnel: [
    { id: "discovered", label: "Postings found", n: 3105 },
    { id: "passed", label: "Passed the US and role filters", n: 220 },
    { id: "reached", label: "Application filled to review", n: 6 },
    { id: "submitted", label: "Submitted", n: 4 },
    { id: "confirmed", label: "Receipt confirmed", n: 3 },
    { id: "responded", label: "Got a response", n: 1 },
    { id: "interview", label: "Interview or offer", n: 0 },
  ],
  skips: [{ reason: "Outside the United States", n: 400 }, { reason: "No target role fits the title", n: 1800 }, { reason: "Title excluded (seniority and similar)", n: 400 }, { reason: "Other", n: 370 }],
  skip_places: [{ place: "London", n: 89 }],
  jobs_by_status: { skipped: 2970, queued: 85, needs_review: 50 },
  jobs_by_company: [{ company: "<b>Acme</b>", found: 100, kept: 10 }],
  jobs_by_role: [{ role: "swe", n: 160 }],
  score_hist: [{ label: "0.5–0.6", n: 3 }, { label: "0.6–0.7", n: 9 }],
  timeline: [{ day: "2026-11-15", found: 12, filled: 1, submitted: 1, responses: 0 }, { day: "2026-11-16", found: 7, filled: 0, submitted: 0, responses: 1 }],
  latency: { discovery_to_fill_h: 30, fill_to_submit_h: 0.4, n_fill: 3, n_submit: 4 },
  ats: { jobs: 2, hist: [{ label: "under 50", n: 0 }, { label: "80–90", n: 2 }], by_site: [{ site: "jobalytics", mean: 84.5, n: 2 }] },
  outcomes: {
    counts: { confirmation: 3, rejection: 1 }, labels: { pending: 2, negative: 1, ghosted: 0 }, resolved: 1, responded: 1,
    rate: { k: 1, n: 1, lo: 0.2, hi: 1 }, median_days: null, cutoff_days: 45, cutoff_basis: "default: too few responses", min_group: 15,
    by_portal: [{ key: "greenhouse", n: 3, resolved: 1, positive: 0, negative: 1, ghosted: 0, pending: 2, response_rate: 1, response_ci: [0.2, 1], positive_rate: 0, positive_ci: [0, 0.8], median_days: null, by_30d: null, too_few: true }],
    by_role: [{ key: "swe", n: 4, resolved: 1, positive: 0, negative: 1, ghosted: 0, pending: 3, response_rate: 1, response_ci: [0.2, 1], positive_rate: 0, positive_ci: [0, 0.8], median_days: null, by_30d: null, too_few: true }], by_ats: [],
  },
};
