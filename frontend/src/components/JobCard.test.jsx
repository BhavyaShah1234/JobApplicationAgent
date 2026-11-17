import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import JobCard from "./JobCard.jsx";
import { JOB } from "../test/mockFetch.js";

test("shows each stage, the bar and the iteration of the running step", () => {
  render(<JobCard job={JOB} />);
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "67");
  expect(screen.getByTestId("stage-scrape")).toHaveClass("done");
  expect(screen.getByTestId("stage-apply")).toHaveClass("pending");
  const docs = screen.getByTestId("stage-documents");
  expect(docs).toHaveClass("running");
  expect(within(docs).getByText("ATS round 2 of 3")).toBeInTheDocument();
  expect(screen.getByText(/step 2 of 3/)).toBeInTheDocument();
  expect(screen.getByText("running")).toBeInTheDocument();
});

test("a failed stage is marked and a finished job fills the bar", () => {
  const failed = { ...JOB, state: "failed", active: false, current: "research", stages: JOB.stages.map((s) => (s.id === "research" ? { ...s, status: "failed", detail: "research failed" } : s)) };
  const { rerender } = render(<JobCard job={failed} />);
  expect(screen.getByTestId("stage-research")).toHaveClass("failed");
  expect(screen.getByText("research failed")).toBeInTheDocument();
  rerender(<JobCard job={{ ...JOB, state: "done", active: false, percent: 95 }} />);
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  expect(screen.getByText("finished")).toBeInTheDocument();
});

test("agent text is shown as text, never as markup", () => {
  const evil = { ...JOB, title: '<img src=x onerror="window.pwned=1">', company: "<script>window.pwned=1</script>" };
  const { container } = render(<JobCard job={evil} />);
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("script")).toBeNull();
  expect(screen.getByText('<img src=x onerror="window.pwned=1">')).toBeInTheDocument();
  expect(window.pwned).toBeUndefined();
});

test("the history opens and closes", async () => {
  render(<JobCard job={JOB} />);
  expect(screen.queryByText(/documents · ATS round 2\/3/)).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "History" }));
  expect(screen.getByText(/documents · ATS round 2\/3/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Hide history" }));
  expect(screen.queryByText(/documents · ATS round 2\/3/)).toBeNull();
});
