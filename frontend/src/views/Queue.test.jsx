import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Queue from "./Queue.jsx";
import { mockFetch } from "../test/mockFetch.js";

const row = (o) => ({ id: 1, url: "https://jobs.example.test/1", company: "Acme", title: "Role 1", status: "queued", position: 1, editable: true, user_edited: false, match_score: 0.8, role_id: "swe", notes: "", updated_at: "2026-11-20T08:00:00Z", applications: 0, ...o });
const QUEUE = [row({}), row({ id: 2, title: "Role 2", position: 2, url: "https://jobs.example.test/2" }), row({ id: 3, title: "Role 3", status: "skipped", position: undefined, url: "https://jobs.example.test/3" }),
               row({ id: 4, title: "Role 4", status: "running", editable: false, position: undefined, url: "https://jobs.example.test/4" })];
const WATCH = [{ id: 7, company: "Zoox", postings: 5, queued: 2, max_jobs: 3, enabled: true, last_run_at: null }];
const routes = (extra = {}) => ({ "/api/queue": QUEUE, "/api/watchlist": WATCH, ...extra });
const body = (fetch, path) => JSON.parse(fetch.mock.calls.filter(([p]) => p === path).at(-1)[1].body);

test("it lists the queue in order with positions, and a running row cannot be edited", async () => {
  mockFetch(routes());
  render(<Queue config={{ read_only: false }} />);
  expect(await screen.findByText("Role 1")).toBeInTheDocument();
  const rows = within(screen.getByRole("table", { name: "Queue" })).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);                                                    // default filter: queued
  await userEvent.click(screen.getByRole("tab", { name: /Running/ }));
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
});

test("pasted links and companies are sent together, and each result is shown", async () => {
  const fetch = mockFetch(routes({ "/api/queue/add": { results: [{ input: "https://x.test/a", result: "added", note: "queued as x" }, { input: "Zoox", result: "exists", note: "already on the list" }] } }));
  render(<Queue config={{ read_only: false }} />);
  await userEvent.type(await screen.findByPlaceholderText(/greenhouse/), "https://x.test/a");
  await userEvent.selectOptions(screen.getByLabelText(/Where in the queue/), "top");
  await userEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(body(fetch, "/api/queue/add")).toEqual({ text: "https://x.test/a", where: "top" });
  expect(await screen.findByText("queued as x")).toBeInTheDocument();
  expect(screen.getByText("already on the list")).toBeInTheDocument();
});

test("the arrows move a job and the new order goes to the server", async () => {
  const fetch = mockFetch(routes({ "/api/queue/move": { position: 1 } }));
  render(<Queue config={{ read_only: false }} />);
  await userEvent.click((await screen.findAllByRole("button", { name: /Move Acme up/ }))[1]);
  expect(body(fetch, "/api/queue/move")).toEqual({ id: 2, to: "up" });
});

test("dragging a row onto another sends the whole new order", async () => {
  const fetch = mockFetch(routes({ "/api/queue/reorder": { count: 2 } }));
  render(<Queue config={{ read_only: false }} />);
  await screen.findByText("Role 1");
  const [first, second] = within(screen.getByRole("table", { name: "Queue" })).getAllByRole("row").slice(1);
  const { fireEvent } = await import("@testing-library/react");
  fireEvent.dragStart(second);
  fireEvent.dragOver(first);
  fireEvent.drop(first);
  await waitFor(() => expect(body(fetch, "/api/queue/reorder")).toEqual({ ids: [2, 1] }));
});

test("selected rows can be set aside in one go", async () => {
  const fetch = mockFetch(routes({ "/api/queue/state": { changed: 2, refused: [] } }));
  render(<Queue config={{ read_only: false }} />);
  await screen.findByText("Role 1");
  await userEvent.click(screen.getByRole("checkbox", { name: "Select Acme Role 1" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Select Acme Role 2" }));
  await userEvent.click(screen.getByRole("button", { name: "Set aside" }));
  expect(body(fetch, "/api/queue/state")).toEqual({ ids: [1, 2], action: "skip" });
  expect(await screen.findByText("Set 2 aside.")).toBeInTheDocument();
});

test("editing a row saves its fields", async () => {
  const fetch = mockFetch(routes({ "/api/queue/edit": { changed: {} } }));
  render(<Queue config={{ read_only: false }} />);
  await screen.findByText("Role 1");
  await userEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
  const title = screen.getByLabelText("Title");
  await userEvent.clear(title);
  await userEvent.type(title, "Staff Engineer");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(body(fetch, "/api/queue/edit")).toMatchObject({ id: 1, title: "Staff Engineer", company: "Acme" });
});

test("the company list adds, runs and shows counts", async () => {
  const fetch = mockFetch(routes({ "/api/watchlist/add": { result: "added", note: "added to the company list" }, "/api/watchlist/run": { pid: 5, note: "watch it" } }));
  render(<Queue config={{ read_only: false }} />);
  expect(await screen.findByText("Zoox")).toBeInTheDocument();
  await userEvent.type(screen.getByPlaceholderText(/Add a company/), "Samsara");
  await userEvent.click(screen.getByRole("button", { name: "Add company" }));
  expect(body(fetch, "/api/watchlist/add")).toEqual({ company: "Samsara" });
  await userEvent.click(screen.getByRole("button", { name: "Run now" }));
  expect(body(fetch, "/api/watchlist/run")).toEqual({ companies: ["Zoox"], limit: 3 });
  expect(await screen.findByText(/Started a run for Zoox/)).toBeInTheDocument();
});

test("running the queue never offers submit", async () => {
  const fetch = mockFetch(routes({ "/api/queue/run": { pid: 9 } }));
  render(<Queue config={{ read_only: false }} />);
  const select = await screen.findByLabelText(/as far as/);
  expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["scrape", "tailor", "apply"]);
  await userEvent.click(screen.getByLabelText(/Skip optional ATS/));
  await userEvent.click(screen.getByRole("button", { name: "Run queue" }));
  expect(body(fetch, "/api/queue/run")).toMatchObject({ through: "apply", no_ats: true });
});

test("read-only hides every control", async () => {
  mockFetch(routes());
  render(<Queue config={{ read_only: true }} />);
  expect(await screen.findByText("Role 1")).toBeInTheDocument();
  for (const name of ["Add", "Run queue", "Edit", "Run now", "Add company"]) expect(screen.queryByRole("button", { name })).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
});

test("a blocked posting has its own tab and can be put back in the queue", async () => {
  const blocked = row({ id: 5, title: "Role 5", status: "blocked", position: undefined, notes: "filling: Why us?", url: "https://jobs.example.test/5" });
  const fetch = mockFetch({ "/api/queue": [...QUEUE, blocked], "/api/watchlist": WATCH, "/api/queue/state": { changed: 1, refused: [] } });
  render(<Queue config={{ read_only: false }} />);
  await userEvent.click(await screen.findByRole("tab", { name: /Blocked/ }));
  expect(await screen.findByText("Role 5")).toBeInTheDocument();
  expect(screen.getByText("filling: Why us?")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("checkbox", { name: "Select Acme Role 5" }));
  await userEvent.click(screen.getByRole("button", { name: "Put in queue" }));
  expect(body(fetch, "/api/queue/state")).toEqual({ ids: [5], action: "queue" });
});
