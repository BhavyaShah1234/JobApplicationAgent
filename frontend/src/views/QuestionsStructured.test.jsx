import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Questions from "./Questions.jsx";
import { mockFetch } from "../test/mockFetch.js";

const spec = (o) => ({ id: "f1", label: "Country of residence", kind: "select", options: ["United States", "Canada", "India"], required: true, section: "", help: "", multiple: false, options_complete: true, current: "", quick: [], ...o });
const req = (o) => ({ id: "r1", prompt: "Example Robotics application asks", secret: false, terminal_only: false, created: "2026-11-20T09:00:00Z", terminal_command: "x", structured: true,
  reply_format: "text", context: { company: "Example Robotics", title: "Robotics Engineer", stage: "apply" }, questions: [spec()], ...o });
const sentBody = (fetch) => JSON.parse(fetch.mock.calls.find(([p]) => p === "/api/hitl/answer")[1].body);

test("it names the company, role and kind, and a dropdown lists every option", async () => {
  mockFetch({ "/api/hitl": [req()] });
  render(<Questions config={{ read_only: false }} />);
  expect(await screen.findByText("Example Robotics")).toBeInTheDocument();
  expect(screen.getByText("Robotics Engineer")).toBeInTheDocument();
  expect(screen.getByText("Dropdown: pick one")).toBeInTheDocument();
  expect(screen.getByText("required")).toBeInTheDocument();
  const select = screen.getByRole("combobox");
  expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choose one… (3 options)", "United States", "Canada", "India"]);
});

test("choosing from the dropdown sends the chosen option under the question id", async () => {
  const fetch = mockFetch({ "/api/hitl": [req()], "/api/hitl/answer": { answered: "r1" } });
  render(<Questions config={{ read_only: false }} />);
  await userEvent.selectOptions(await screen.findByRole("combobox"), "Canada");
  await userEvent.click(screen.getByRole("button", { name: "Send answer" }));
  expect(sentBody(fetch)).toEqual({ id: "r1", answers: { f1: "Canada" } });
  await waitFor(() => expect(screen.getByText("Sent.")).toBeInTheDocument());
});

test("radio buttons show all choices and one can be cleared", async () => {
  mockFetch({ "/api/hitl": [req({ questions: [spec({ kind: "radio", options: ["Yes", "No"], label: "Authorized to work?" })] })] });
  render(<Questions config={{ read_only: false }} />);
  const yes = await screen.findByRole("radio", { name: "Yes" });
  expect(screen.getByRole("radio", { name: "No" })).toBeInTheDocument();
  await userEvent.click(yes);
  expect(yes).toBeChecked();
  await userEvent.click(screen.getByRole("button", { name: "Clear" }));
  expect(yes).not.toBeChecked();
});

test("a batch of mixed kinds is answered together and sent as one object", async () => {
  const fetch = mockFetch({ "/api/hitl": [req({ reply_format: "json", questions: [
    spec({ id: "a", label: "Heard about us", kind: "checkboxes", options: ["LinkedIn", "Referral"], multiple: true }),
    spec({ id: "b", label: "Start date", kind: "text", options: [] }),
    spec({ id: "c", label: "Salary", kind: "number", options: [], required: false }),
  ] })], "/api/hitl/answer": { answered: "r1" } });
  render(<Questions config={{ read_only: false }} />);
  await userEvent.click(await screen.findByRole("checkbox", { name: "LinkedIn" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Referral" }));
  await userEvent.type(screen.getByLabelText("Start date"), "Jan 2027");
  expect(screen.getByText("Tick any that apply")).toBeInTheDocument();
  expect(screen.getByText("Number")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Send 2 of 3 answers" }));
  expect(sentBody(fetch).answers).toEqual({ a: ["LinkedIn", "Referral"], b: "Jan 2027", c: "" });
});

test("in a batch there is no per-question Skip button (empty means skip)", async () => {
  mockFetch({ "/api/hitl": [req({ reply_format: "json", questions: [spec({ id: "a", quick: ["skip"] }), spec({ id: "b", label: "Other", quick: ["skip"] })] })] });
  render(<Questions config={{ read_only: false }} />);
  await screen.findByText("Other");
  expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
  expect(screen.getByRole("button", { name: "Skip all" })).toBeInTheDocument();
});

test("skip sends empty answers, and a quick button sends its word", async () => {
  const fetch = mockFetch({ "/api/hitl": [req({ questions: [spec({ quick: ["skip"] })] })], "/api/hitl/answer": { answered: "r1" } });
  render(<Questions config={{ read_only: false }} />);
  await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
  expect(sentBody(fetch).answers).toEqual({ f1: "skip" });
});

test("a refused answer shows the server's reason and keeps what was chosen", async () => {
  mockFetch({ "/api/hitl": [req()], "/api/hitl/answer": { __status: 400, body: { error: "'Atlantis' is not one of the options" } } });
  render(<Questions config={{ read_only: false }} />);
  await userEvent.selectOptions(await screen.findByRole("combobox"), "India");
  await userEvent.click(screen.getByRole("button", { name: "Send answer" }));
  expect(await screen.findByText(/not one of the options/)).toBeInTheDocument();
  expect(screen.getByRole("combobox")).toHaveValue("India");
});

test("a terminal-only question shows its options but no way to answer in the page", async () => {
  mockFetch({ "/api/hitl": [req({ terminal_only: true })] });
  render(<Questions config={{ read_only: false }} />);
  expect(await screen.findByText("This decision requires a terminal:")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Send answer" })).toBeNull();
  expect(screen.getByRole("combobox")).toBeDisabled();
});

test("a plain prompt with a quoted one-word reply gets a button for it", async () => {
  const fetch = mockFetch({ "/api/hitl": [{ id: "p1", prompt: "Do it in noVNC then answer 'done'", secret: false, created: "2026-11-20T09:00:00Z", terminal_command: "x",
    structured: false, questions: [{ id: "reply", label: "x", kind: "text", options: [], quick: ["done"] }], context: {} }], "/api/hitl/answer": { answered: "p1" } });
  render(<Questions config={{ read_only: false }} />);
  await userEvent.click(await screen.findByRole("button", { name: "Done" }));
  expect(sentBody(fetch)).toEqual({ id: "p1", value: "done" });
});

test("a radio whose only option is its own label warns that the question text was not read", async () => {
  mockFetch({ "/api/hitl": [req({ questions: [spec({ kind: "radio", label: "Yes", options: ["Yes"] })] })] });
  render(<Questions config={{ read_only: false }} />);
  expect(await screen.findByText(/could not read the question this choice belongs to/)).toBeInTheDocument();
});
