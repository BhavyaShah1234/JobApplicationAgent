import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import Queue from "./Queue.jsx";
import { mockFetch } from "../test/mockFetch.js";

const ROW = (id, status, extra = {}) => ({ id, company: `Co${id}`, title: `Role ${id}`, status, has_application: false, role_id: "swe", match_score: 0.7, updated_at: "2026-11-17T09:00:00Z", notes: "", url: `https://jobs.example.test/${id}`, ...extra });
const ROWS = [ROW(1, "queued"), ROW(2, "needs_review"), ROW(3, "running"), ROW(4, "applied"), ROW(5, "queued", { has_application: true }), ROW(6, "skipped")];
const cfg = { read_only: false };

function setup(extra = {}) {
  const fetch = mockFetch({ "/api/queue": ROWS, "/api/queue/delete": { deleted: 1, refused: [] }, "/api/run": { pid: 77, note: "Runs in the background." }, ...extra });
  render(<Queue config={cfg} onOpenJobs={vi.fn()} />);
  return fetch;
}

test("the start form is on the queue page and starts a run", async () => {
  const fetch = setup();
  await screen.findByText("Co1");
  await userEvent.type(screen.getByPlaceholderText(/One per line/), "https://jobs.example.test/9{enter}Acme Robotics");
  await userEvent.click(screen.getByRole("button", { name: "Start" }));
  const call = fetch.mock.calls.find(([p]) => p === "/api/run");
  expect(call[1].headers["X-Dashboard"]).toBe("1");
  expect(JSON.parse(call[1].body)).toMatchObject({ inputs: ["https://jobs.example.test/9", "Acme Robotics"], through: "apply", limit: 5, browser_mode: "auto" });
  expect(await screen.findByText(/Started \(pid 77\)/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Watch it in Jobs" })).toBeInTheDocument();
});

test("only waiting postings without an application can be selected", async () => {
  setup();
  await screen.findByText("Co1");
  for (const [name, enabled] of [["Co1 Role 1", true], ["Co2 Role 2", true], ["Co3 Role 3", false], ["Co4 Role 4", false], ["Co5 Role 5", false], ["Co6 Role 6", true]])
    expect(screen.getByRole("checkbox", { name: `Select ${name}` }).disabled).toBe(!enabled);
});

test("select all picks every deletable posting shown and the status buttons narrow the table", async () => {
  setup();
  await screen.findByText("Co1");
  await userEvent.click(screen.getByRole("checkbox", { name: /Select all deletable/ }));
  expect(screen.getByRole("button", { name: "Delete selected (3)" })).toBeEnabled();
  await userEvent.click(screen.getByRole("button", { name: "queued (2)" }));
  expect(screen.getByText("2 of 6 postings")).toBeInTheDocument();
  expect(screen.queryByText("Co2")).toBeNull();
});

test("deleting asks first, sends the chosen ids, and reports what was kept", async () => {
  const fetch = setup({ "/api/queue/delete": { deleted: 1, refused: [{ id: 2, why: "it has an application on record" }] } });
  await screen.findByText("Co1");
  await userEvent.click(screen.getByRole("checkbox", { name: "Select Co1 Role 1" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Select Co2 Role 2" }));
  await userEvent.click(screen.getByRole("button", { name: "Delete selected (2)" }));
  const dialog = screen.getByRole("alertdialog");
  expect(fetch.mock.calls.some(([p]) => p === "/api/queue/delete")).toBe(false);          // nothing is sent before the confirmation
  expect(dialog).toHaveTextContent("Delete 2 postings");
  await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
  const call = fetch.mock.calls.find(([p]) => p === "/api/queue/delete");
  expect(call[1].headers["X-Dashboard"]).toBe("1");
  expect(JSON.parse(call[1].body)).toEqual({ ids: [1, 2] });
  expect(await screen.findByText(/Deleted 1 posting\. Kept 1: it has an application on record/)).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
});

test("a row's own Delete button deletes only that posting, and Cancel sends nothing", async () => {
  const fetch = setup();
  await screen.findByText("Co6");
  await userEvent.click(screen.getByRole("button", { name: "Delete Co6 Role 6" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  expect(fetch.mock.calls.some(([p]) => p === "/api/queue/delete")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Delete Co6 Role 6" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
  expect(JSON.parse(fetch.mock.calls.find(([p]) => p === "/api/queue/delete")[1].body)).toEqual({ ids: [6] });
});

test("running, applied and recorded postings have no Delete button", async () => {
  setup();
  await screen.findByText("Co3");
  for (const n of [3, 4, 5]) expect(screen.queryByRole("button", { name: `Delete Co${n} Role ${n}` })).toBeNull();
});

test("a server refusal is shown and the confirmation closes", async () => {
  setup({ "/api/queue/delete": { __status: 400, body: { error: "give a list of 1 to 500 job ids" } } });
  await screen.findByText("Co1");
  await userEvent.click(screen.getByRole("button", { name: "Delete Co1 Role 1" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
  expect(await screen.findByText("give a list of 1 to 500 job ids")).toBeInTheDocument();
});

test("a read-only dashboard has neither the start form nor any delete control", async () => {
  mockFetch({ "/api/queue": ROWS });
  render(<Queue config={{ read_only: true }} />);
  await screen.findByText("Co1");
  expect(screen.queryByPlaceholderText(/One per line/)).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
  expect(screen.getByText(/Read-only view/)).toBeInTheDocument();
});

test("posting titles link out safely and an unsafe address is plain text", async () => {
  mockFetch({ "/api/queue": [ROW(1, "queued"), ROW(2, "queued", { url: "javascript:alert(1)" })] });
  render(<Queue config={cfg} />);
  const link = await screen.findByRole("link", { name: "Role 1" });
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(screen.queryByRole("link", { name: "Role 2" })).toBeNull();
});
