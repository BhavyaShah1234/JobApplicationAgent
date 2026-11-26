import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PresenceToggle from "./PresenceToggle.jsx";
import { mockFetch } from "../test/mockFetch.js";

test("switching off tells the server the candidate is away, with the guard header", async () => {
  const fetch = mockFetch({ "/api/presence": { available: false } });
  let changed = 0;
  render(<PresenceToggle presence={{ available: true }} onChange={() => changed++} />);
  const sw = screen.getByRole("switch", { name: "Available for questions" });
  expect(sw).toHaveAttribute("aria-checked", "true");
  await userEvent.click(sw);
  const call = fetch.mock.calls.find(([p]) => p === "/api/presence");
  expect(call[1].method).toBe("POST");
  expect(call[1].headers["X-Dashboard"]).toBe("1");
  expect(JSON.parse(call[1].body)).toEqual({ available: false });
  await waitFor(() => expect(changed).toBe(1));
});

test("away shows how many jobs wait for the candidate; switching on sends available", async () => {
  const fetch = mockFetch({ "/api/presence": { available: true } });
  render(<PresenceToggle presence={{ available: false }} deferred={2} />);
  expect(screen.getByText("Away")).toBeInTheDocument();
  expect(screen.getByText("2 waiting")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("switch"));
  expect(JSON.parse(fetch.mock.calls.find(([p]) => p === "/api/presence")[1].body)).toEqual({ available: true });
});

test("a read-only dashboard cannot switch it, and a refused change is shown", async () => {
  const { rerender } = render(<PresenceToggle presence={{ available: true }} readOnly />);
  expect(screen.getByRole("switch")).toBeDisabled();
  mockFetch({ "/api/presence": { __status: 403, body: { error: "this dashboard is read-only" } } });
  rerender(<PresenceToggle presence={{ available: true }} />);
  await userEvent.click(screen.getByRole("switch"));
  await waitFor(() => expect(screen.getByText(/read-only/)).toBeInTheDocument());
  expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");      // unchanged after the refusal
});
