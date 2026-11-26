import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import Questions from "./Questions.jsx";
import { mockFetch } from "../test/mockFetch.js";

const Q = { id: "ab12", prompt: "Are you open to relocating?", secret: false, default: null, created: "2026-11-17T09:00:00Z", terminal_command: "jobagent hitl answer ab12 '<answer>'" };
const SECRET = { ...Q, id: "cd34", prompt: "Portal password", secret: true, terminal_command: "jobagent hitl answer cd34" };

afterEach(() => vi.useRealTimers());

test("an answer being typed survives the page refreshing its data", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetch = mockFetch({ "/api/hitl": [Q] });
  render(<Questions config={{ read_only: false }} />);
  const box = await screen.findByPlaceholderText("your answer");
  await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).type(box, "Yes, Denver is fine.");
  const before = fetch.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(16000);                                 // three refreshes
  });
  expect(fetch.mock.calls.length).toBeGreaterThan(before);
  expect(screen.getByPlaceholderText("your answer")).toHaveValue("Yes, Denver is fine.");
});

test("sending posts the answer with the guard header", async () => {
  const fetch = mockFetch({ "/api/hitl": [Q], "/api/hitl/answer": { answered: "ab12" } });
  render(<Questions config={{ read_only: false }} />);
  await userEvent.type(await screen.findByPlaceholderText("your answer"), "Yes");
  await userEvent.click(screen.getByRole("button", { name: "Send answer" }));
  const call = fetch.mock.calls.find(([p]) => p === "/api/hitl/answer");
  expect(call[1].method).toBe("POST");
  expect(call[1].headers["X-Dashboard"]).toBe("1");
  expect(JSON.parse(call[1].body)).toEqual({ id: "ab12", value: "Yes" });
  await waitFor(() => expect(screen.getByText("Sent.")).toBeInTheDocument());
});

test("a secret, or a read-only dashboard, shows the terminal command instead of a box", async () => {
  mockFetch({ "/api/hitl": [SECRET, Q] });
  const { rerender } = render(<Questions config={{ read_only: false }} />);
  expect(await screen.findByText("jobagent hitl answer cd34")).toBeInTheDocument();
  expect(screen.getAllByPlaceholderText("your answer")).toHaveLength(1);           // only the ordinary question has a box
  rerender(<Questions config={{ read_only: true }} />);
  await waitFor(() => expect(screen.queryByPlaceholderText("your answer")).toBeNull());
});

test('submission approval cannot be entered in the ordinary answer box', async () => {
  mockFetch({ '/api/hitl': [{ ...Q, terminal_only: true, prompt: 'Review and approve submission in a terminal' }] });
  render(<Questions config={{ read_only: false }} />);
  expect(await screen.findByText('This decision requires a terminal:')).toBeInTheDocument();
  expect(screen.queryByPlaceholderText('your answer')).toBeNull();
});

const SAVED = [
  { job: "finthrive/6502", key: "k1", company: "FinThrive", title: "Software Engineer", label: "Do you have Angular experience?",
    section: "Questions", required: true, options: ["Yes", "No"], status: "open", first_asked: "2026-11-26T09:00:00Z" },
  { job: "finthrive/6502", key: "k2", company: "FinThrive", title: "Software Engineer", label: "What is your desired salary for this role?",
    section: "Questions", required: true, options: [], status: "open", first_asked: "2026-11-26T09:00:00Z" },
];

test("saved questions are grouped by application and an answer is saved for the next run", async () => {
  const fetch = mockFetch({ "/api/hitl": [], "/api/saved-questions": SAVED, "/api/saved-questions/answer": { answered: "k2" } });
  render(<Questions config={{ read_only: false }} />);
  expect(await screen.findByText("Saved for later")).toBeInTheDocument();
  expect(screen.getByText("FinThrive · Software Engineer")).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("Answer: Do you have Angular experience?"), "No");
  await userEvent.type(screen.getByLabelText("Answer: What is your desired salary for this role?"), "135980");
  await userEvent.click(screen.getAllByRole("button", { name: "Save answer" })[1]);
  const call = fetch.mock.calls.find(([p]) => p === "/api/saved-questions/answer");
  expect(call[1].headers["X-Dashboard"]).toBe("1");
  expect(JSON.parse(call[1].body)).toEqual({ job: "finthrive/6502", key: "k2", value: "135980" });
  await waitFor(() => expect(screen.getByText("Saved for the next run.")).toBeInTheDocument());
});

test("a read-only dashboard shows saved questions without an answer box", async () => {
  mockFetch({ "/api/hitl": [], "/api/saved-questions": SAVED });
  render(<Questions config={{ read_only: true }} />);
  expect(await screen.findByText("Do you have Angular experience?")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save answer" })).toBeNull();
});
