import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App.jsx";
import { JOB, mockFetch } from "./test/mockFetch.js";

const OVERVIEW = { jobs: { queued: 2 }, applications: { filled: 1 }, outcomes: {}, waiting: 2, ready: 1, questions: 3, runs_active: 1, jobs_active: 1, read_only: false };

beforeEach(() => {
  location.hash = "";
});

test("the header shows the tabs with their counts and the overview shows what is running", async () => {
  mockFetch({ "/api/overview": OVERVIEW, "/api/config": { read_only: false, novnc: "" }, "/api/pipeline": [JOB] });
  render(<App />);
  expect(await screen.findByText("Robotics Engineer")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /Questions/ })).toHaveTextContent("3");
  expect(screen.getByRole("tab", { name: /Review/ })).toHaveTextContent("2");
  expect(screen.getByRole("tab", { name: "Start a run" })).toBeInTheDocument();
  expect(screen.getByText(/live/)).toBeInTheDocument();
});

test("a read-only dashboard has no Start tab", async () => {
  mockFetch({ "/api/overview": { ...OVERVIEW, read_only: true }, "/api/config": { read_only: true, novnc: "" }, "/api/pipeline": [] });
  render(<App />);
  await screen.findByText(/Read-only view/);
  expect(screen.queryByRole("tab", { name: "Start a run" })).toBeNull();
});

test("tabs switch the view", async () => {
  mockFetch({ "/api/overview": OVERVIEW, "/api/config": { read_only: false, novnc: "" }, "/api/pipeline": [JOB], "/api/queue": [{ id: 1, company: "Acme", title: "Dev", status: "queued", role_id: "swe", match_score: 0.9, notes: "", updated_at: "2026-11-17T09:00:00Z" }] });
  render(<App />);
  await userEvent.click(await screen.findByRole("tab", { name: "Queue" }));
  expect(await screen.findByText("Acme")).toBeInTheDocument();
  await userEvent.type(screen.getByPlaceholderText(/Filter by company/), "zzz");
  await waitFor(() => expect(screen.queryByText("Acme")).toBeNull());
});

test("a 401 shows how to sign in", async () => {
  mockFetch({ "/api/overview": { __status: 401 }, "/api/config": { __status: 401 } });
  render(<App />);
  expect(await screen.findByText(/Signed out/)).toBeInTheDocument();
});
