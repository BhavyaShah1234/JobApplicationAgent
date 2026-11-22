import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App.jsx";
import { JOB, mockFetch } from "./test/mockFetch.js";

const OVERVIEW = { jobs: { queued: 2 }, applications: { filled: 1 }, outcomes: {}, waiting: 2, ready: 1, questions: 3, runs_active: 1, jobs_active: 1, read_only: false };

beforeEach(() => {
  history.replaceState(null, "", "/");
});

test("the header shows the tabs with their counts and the overview shows what is running", async () => {
  mockFetch({ "/api/overview": OVERVIEW, "/api/config": { read_only: false, novnc: "" }, "/api/pipeline": [JOB] });
  render(<App />);
  expect(await screen.findByText("Robotics Engineer")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /Questions/ })).toHaveTextContent("3");
  expect(screen.getByRole("tab", { name: /Review/ })).toHaveTextContent("2");
  expect(screen.queryByRole("tab", { name: "Start a run" })).toBeNull();                 // the start form lives on the Queue page now
  expect(screen.getByText(/live/)).toBeInTheDocument();
});

test("a read-only dashboard shows the queue without the start form or delete buttons", async () => {
  history.replaceState(null, "", "/queue");
  mockFetch({ "/api/overview": { ...OVERVIEW, read_only: true }, "/api/config": { read_only: true, novnc: "" }, "/api/pipeline": [],
    "/api/queue": [{ id: 1, company: "Acme", title: "Dev", status: "queued", has_application: false, updated_at: "2026-11-17T09:00:00Z" }] });
  render(<App />);
  expect(await screen.findByText("Acme")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
  expect(screen.queryByPlaceholderText(/One per line/)).toBeNull();
  expect(screen.queryByRole("tab", { name: "Start a run" })).toBeNull();
});

test("the old /start address opens the Queue page", async () => {
  history.replaceState(null, "", "/start");
  mockFetch({ "/api/overview": OVERVIEW, "/api/config": { read_only: false, novnc: "" }, "/api/pipeline": [], "/api/queue": [] });
  render(<App />);
  expect(await screen.findByPlaceholderText(/One per line/)).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Queue" })).toHaveAttribute("aria-selected", "true");
  expect(location.pathname).toBe("/queue");
});

test("tabs switch the view", async () => {
  mockFetch({ "/api/overview": OVERVIEW, "/api/config": { read_only: false, novnc: "" }, "/api/pipeline": [JOB], "/api/queue": [{ id: 1, company: "Acme", title: "Dev", status: "queued", role_id: "swe", match_score: 0.9, notes: "", updated_at: "2026-11-17T09:00:00Z" }] });
  render(<App />);
  await userEvent.click(await screen.findByRole("tab", { name: "Queue" }));
  expect(await screen.findByText("Acme")).toBeInTheDocument();
  expect(location.pathname).toBe("/queue");
  expect(location.hash).toBe("");
  expect(screen.getByRole("tab", { name: /Jobs/ })).toHaveAttribute("href", "/jobs");
  await userEvent.type(screen.getByPlaceholderText(/Filter by company/), "zzz");
  await waitFor(() => expect(screen.queryByText("Acme")).toBeNull());
});

test("a direct page URL renders the selected view and browser history changes it", async () => {
  history.replaceState(null, "", "/jobs");
  mockFetch({ "/api/overview": OVERVIEW, "/api/config": { read_only: false, novnc: "" }, "/api/pipeline": [] });
  render(<App />);
  expect(await screen.findByText("No jobs in this view yet.")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /Jobs/ })).toHaveAttribute("aria-selected", "true");
  await userEvent.click(screen.getByRole("tab", { name: /Questions/ }));
  expect(location.pathname).toBe("/questions");
  history.back();
  await waitFor(() => expect(screen.getByRole("tab", { name: /Jobs/ })).toHaveAttribute("aria-selected", "true"));
  expect(location.pathname).toBe("/jobs");
});

test("an old hash bookmark is replaced with a real path", async () => {
  history.replaceState(null, "", "/#jobs");
  mockFetch({ "/api/overview": OVERVIEW, "/api/config": { read_only: false, novnc: "" }, "/api/pipeline": [] });
  render(<App />);
  expect(await screen.findByText("No jobs in this view yet.")).toBeInTheDocument();
  expect(location.pathname).toBe("/jobs");
  expect(location.hash).toBe("");
});

test("a 401 shows how to sign in", async () => {
  mockFetch({ "/api/overview": { __status: 401 }, "/api/config": { __status: 401 } });
  render(<App />);
  expect(await screen.findByText(/Signed out/)).toBeInTheDocument();
});
