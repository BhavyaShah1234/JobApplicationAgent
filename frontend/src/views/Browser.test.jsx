import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Browser, { embedUrl } from "./Browser.jsx";
import JobCard from "../components/JobCard.jsx";
import { JOB, mockFetch } from "../test/mockFetch.js";

const URL_ = "http://localhost:6082/vnc.html";

test("the embed address asks noVNC to connect, scale to the panel and, when read-only, only watch", () => {
  const rw = new URL(embedUrl(URL_, false));
  expect(rw.origin + rw.pathname).toBe(URL_);
  expect(rw.searchParams.get("autoconnect")).toBe("true");
  expect(rw.searchParams.get("resize")).toBe("scale");
  expect(rw.searchParams.get("view_only")).toBeNull();
  expect(new URL(embedUrl(URL_, true)).searchParams.get("view_only")).toBe("true");
});

test("a reachable browser view is framed inside the page, sandboxed", async () => {
  mockFetch({ "/api/vnc": { url: URL_, reachable: true } });
  render(<Browser config={{ novnc: URL_, read_only: false }} />);
  const frame = await screen.findByTitle("Agent browser");
  expect(frame.getAttribute("src")).toContain("localhost:6082/vnc.html");
  expect(frame.getAttribute("sandbox")).toBe("allow-scripts allow-same-origin");           // no top navigation, popups or forms
  expect(await screen.findByText("connected")).toBeInTheDocument();
});

test("an unreachable one says where it looked and what to check instead of an empty frame", async () => {
  mockFetch({ "/api/vnc": { url: URL_, reachable: false } });
  render(<Browser config={{ novnc: URL_, read_only: false }} />);
  expect(await screen.findByText(/Nothing answers at/)).toBeInTheDocument();
  expect(screen.queryByTitle("Agent browser")).toBeNull();
});

test("without an address the tab explains how to set one", () => {
  mockFetch({ "/api/vnc": { url: "", reachable: false } });
  render(<Browser config={{ novnc: "", read_only: false }} />);
  expect(screen.getByText(/--novnc-url/)).toBeInTheDocument();
});

test("a job that is driving a web page offers a way to watch it", async () => {
  const onWatch = vi.fn();
  const filling = { ...JOB, current: "apply", stages: JOB.stages.map((s) => (s.id === "apply" ? { ...s, status: "running", step: "Form page", i: 2, n: 15 } : s)) };
  const { rerender } = render(<JobCard job={filling} onWatch={onWatch} />);
  await userEvent.click(screen.getByRole("button", { name: "Watch the browser" }));
  expect(onWatch).toHaveBeenCalled();
  rerender(<JobCard job={JOB} onWatch={onWatch} />);                                        // writing documents: no browser involved
  expect(screen.queryByRole("button", { name: "Watch the browser" })).toBeNull();
});
