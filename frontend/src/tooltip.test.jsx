import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useTip, TipBody, TipProvider } from "./tooltip.jsx";

function Thing() {
  const tip = useTip();
  return (
    <>
      <div {...tip(<TipBody title="Alpha" rows={[["Count", "7"]]} note="a note" />, "alpha")}>first</div>
      <div {...tip(<TipBody title="Beta" rows={[["Count", "9"]]} />, "beta")}>second</div>
      <p>outside</p>
    </>
  );
}

test("hovering shows the tooltip's title, rows and note, and leaving hides it", async () => {
  render(<TipProvider><Thing /></TipProvider>);
  expect(screen.queryByRole("tooltip")).toBeNull();
  await userEvent.hover(screen.getByText("first"));
  const t = screen.getByRole("tooltip");
  expect(t).toHaveTextContent("Alpha");
  expect(t).toHaveTextContent("Count7");
  expect(t).toHaveTextContent("a note");
  await userEvent.unhover(screen.getByText("first"));
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("keyboard focus shows it too, and Escape hides it", async () => {
  render(<TipProvider><Thing /></TipProvider>);
  await userEvent.tab();
  expect(screen.getByRole("tooltip")).toHaveTextContent("Alpha");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("tooltip")).toBeNull();
  await userEvent.tab();
  expect(screen.getByRole("tooltip")).toHaveTextContent("Beta");
});

test("a tap shows it and a tap elsewhere hides it", async () => {
  render(<TipProvider><Thing /></TipProvider>);
  fireEvent.click(screen.getByText("second"));
  expect(screen.getByRole("tooltip")).toHaveTextContent("Beta");
  fireEvent.pointerDown(screen.getByText("outside"));
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("scrolling hides it, and the elements are focusable and named for screen readers", async () => {
  render(<TipProvider><Thing /></TipProvider>);
  const el = screen.getByText("first");
  expect(el).toHaveAttribute("tabindex", "0");
  expect(el).toHaveAttribute("aria-label", "alpha");
  await userEvent.hover(el);
  act(() => { window.dispatchEvent(new Event("scroll")); });
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("without a provider a component still renders and has no handlers", () => {
  render(<Thing />);
  expect(screen.getByText("first")).not.toHaveAttribute("tabindex");
});
