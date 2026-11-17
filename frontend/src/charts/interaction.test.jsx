import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TipProvider } from "../tooltip.jsx";
import BarList from "./BarList.jsx";
import FunnelChart from "./FunnelChart.jsx";
import Histogram from "./Histogram.jsx";
import IntervalDot from "./IntervalDot.jsx";
import StackedBar from "./StackedBar.jsx";
import TimelineChart from "./TimelineChart.jsx";
import TwoToneBars from "./TwoToneBars.jsx";
import { ANALYTICS } from "../test/mockFetch.js";

const wrap = (ui) => render(<TipProvider>{ui}</TipProvider>);

test("a funnel step says its share of everything, what it kept, and how many dropped out", async () => {
  wrap(<FunnelChart steps={ANALYTICS.funnel} />);
  await userEvent.hover(screen.getByText("Passed the US and role filters"));
  const t = screen.getByRole("tooltip");
  expect(t).toHaveTextContent("Share of postings found7%");
  expect(t).toHaveTextContent("Kept from the step above7%");
  expect(t).toHaveTextContent("Dropped out here2,885");                    // 3105 - 220
});

test("a bar in a list gives its count and its share of the list", async () => {
  wrap(<BarList label="x" unit="postings" items={[{ label: "Toronto", n: 30 }, { label: "Dublin", n: 10 }]} />);
  await userEvent.hover(screen.getByText("Toronto"));
  expect(screen.getByRole("tooltip")).toHaveTextContent("Count (postings)30");
  expect(screen.getByRole("tooltip")).toHaveTextContent("Share of this list75%");
});

test("a company bar gives found, passed and the share that passed", async () => {
  wrap(<TwoToneBars label="c" items={[{ label: "Acme", n: 40, part: 10 }]} />);
  await userEvent.hover(screen.getByText("Acme"));
  const t = screen.getByRole("tooltip");
  expect(t).toHaveTextContent("Found40");
  expect(t).toHaveTextContent("Passed the filters10");
  expect(t).toHaveTextContent("Share that passed25%");
});

test("a histogram bar dims the others and shows its share", async () => {
  const { container } = wrap(<Histogram label="h" bins={[{ label: "a", n: 1 }, { label: "b", n: 3 }]} />);
  await userEvent.hover(container.querySelectorAll(".hcol")[1]);
  expect(container.querySelector(".hist")).toHaveClass("has-active");
  expect(container.querySelectorAll(".hcol")[1]).toHaveClass("active");
  expect(container.querySelectorAll(".hcol")[0]).not.toHaveClass("active");
  expect(screen.getByRole("tooltip")).toHaveTextContent("Share of all75%");
  await userEvent.unhover(container.querySelectorAll(".hcol")[1]);
  expect(container.querySelector(".hist")).not.toHaveClass("has-active");
});

test("a part of the stacked bar and its legend row light up together", async () => {
  const { container } = wrap(<StackedBar label="s" segments={[{ label: "Outside the US", n: 60 }, { label: "Queue", n: 40 }]} />);
  await userEvent.hover(screen.getByText("Queue", { selector: ".sl" }));
  expect(container.querySelectorAll(".seg")[1]).toHaveClass("active");
  expect(container.querySelectorAll(".slegend li")[1]).toHaveClass("active");
  await userEvent.unhover(screen.getByText("Queue", { selector: ".sl" }));
  await userEvent.hover(container.querySelectorAll(".seg")[0]);
  expect(container.querySelectorAll(".slegend li")[0]).toHaveClass("active");
  expect(screen.getByRole("tooltip")).toHaveTextContent("Share of all60%");
});

test("pointing at a day in the timeline shows that day's numbers; the arrow keys move between days", async () => {
  const { container } = wrap(<TimelineChart days={ANALYTICS.timeline} />);
  fireEvent.mouseEnter(screen.getAllByTestId("day-hit")[0], { clientX: 40, clientY: 40 });
  let t = screen.getByRole("tooltip");
  expect(t).toHaveTextContent("2026-11-15");
  expect(t).toHaveTextContent("Postings found12");
  expect(container.querySelector(".guide")).toBeInTheDocument();
  const svg = screen.getByRole("img");
  svg.focus();
  fireEvent.keyDown(svg, { key: "ArrowRight" });
  expect(container.querySelector(".sr-only")).toHaveTextContent(/2026-11-16: 7 postings found/);
  fireEvent.keyDown(svg, { key: "Escape" });
  expect(container.querySelector(".guide")).toBeNull();
});

test("an interval says what the rate is made of and warns when it is too thin", async () => {
  const { container } = wrap(<IntervalDot name="greenhouse" rate={1} ci={[0.2, 1]} k={3} n={3} tooFew />);
  await userEvent.hover(container.querySelector(".interval"));
  const t = screen.getByRole("tooltip");
  expect(t).toHaveTextContent("greenhouse");
  expect(t).toHaveTextContent("Replied of resolved3 of 3");
  expect(t).toHaveTextContent("95% interval20% to 100%");
  expect(t).toHaveTextContent("Too few resolved applications");
});
