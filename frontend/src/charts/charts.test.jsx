import { render, screen } from "@testing-library/react";
import BarList from "./BarList.jsx";
import FunnelChart from "./FunnelChart.jsx";
import Histogram from "./Histogram.jsx";
import IntervalDot from "./IntervalDot.jsx";
import TimelineChart from "./TimelineChart.jsx";
import { TipProvider } from "../tooltip.jsx";
import { ANALYTICS } from "../test/mockFetch.js";

test("the funnel shows each step with the share kept from the one above and a readable description", () => {
  render(<FunnelChart steps={ANALYTICS.funnel} />);
  expect(screen.getByRole("img")).toHaveAccessibleName(/Postings found 3105/);
  expect(screen.getByText("Passed the US and role filters")).toBeInTheDocument();
  expect(screen.getByText(/(^|\s)7% of previous/)).toBeInTheDocument();        // 220 of 3105
  expect(screen.getByText(/(^|\s)75% of previous/)).toBeInTheDocument();       // 3 receipts of 4 submitted
  expect(screen.getByText(/(^|\s)3% of previous/)).toBeInTheDocument();        // 6 filled of 220 kept
});

test("a funnel of zeros does not divide by zero", () => {
  render(<FunnelChart steps={[{ id: "a", label: "A", n: 0 }, { id: "b", label: "B", n: 0 }]} />);
  expect(screen.getByText("A")).toBeInTheDocument();
});

test("the timeline draws a bar per non-zero day and series, with a tooltip, and says so when empty", () => {
  const { container, rerender } = render(<TimelineChart days={ANALYTICS.timeline} />);
  expect(container.querySelectorAll("rect:not([data-testid])").length).toBe(5);                  // found x2, filled, submitted, responses
  expect(container.querySelector("title").textContent).toMatch(/2026-11-15: 12 postings found/);
  rerender(<TimelineChart days={[{ day: "2026-11-15", found: 0, filled: 0, submitted: 0, responses: 0 }]} />);
  expect(screen.getByText("No activity in this period.")).toBeInTheDocument();
});

test("a rate shows its interval, and a group with too few applications is marked and greyed", () => {
  const { container, rerender } = render(<TipProvider><IntervalDot rate={0.5} ci={[0.2, 0.8]} tooFew={false} /></TipProvider>);
  expect(screen.getByText("50%")).toBeInTheDocument();
  expect(container.querySelector(".interval")).not.toHaveClass("few");
  expect(container.querySelector(".interval").getAttribute("aria-label")).toMatch(/95% interval 20% to 80%/);
  rerender(<TipProvider><IntervalDot rate={1} ci={[0.2, 1]} tooFew /></TipProvider>);
  expect(container.querySelector(".interval")).toHaveClass("few");
  expect(container.querySelector(".interval").getAttribute("aria-label")).toMatch(/too few to trust/);
  rerender(<TipProvider><IntervalDot rate={null} ci={null} /></TipProvider>);
  expect(screen.getByText("–")).toBeInTheDocument();
});

test("bars and histograms handle data and nothing", () => {
  const { rerender } = render(<BarList label="x" items={[{ label: "a", n: 5 }, { label: "b", n: 1 }]} />);
  expect(screen.getByRole("img")).toHaveAccessibleName("x: a 5, b 1");
  rerender(<BarList label="x" items={[]} />);
  expect(screen.getByText("Nothing to show yet.")).toBeInTheDocument();
  rerender(<Histogram label="h" bins={[{ label: "1", n: 0 }, { label: "2", n: 0 }]} />);
  expect(screen.getByText("Nothing to show yet.")).toBeInTheDocument();
});
