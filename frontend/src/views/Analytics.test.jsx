import { render, screen, within } from "@testing-library/react";
import Analytics from "./Analytics.jsx";
import { ANALYTICS, mockFetch } from "../test/mockFetch.js";

test("it shows the numbers, the funnel, and warns that few applications means few conclusions", async () => {
  mockFetch({ "/api/analytics": ANALYTICS });
  render(<Analytics />);
  expect(await screen.findByText(/Only 4 applications submitted so far/)).toBeInTheDocument();
  expect(screen.getAllByText("3,200").length).toBeGreaterThan(0);                  // the headline number and the funnel
  expect(screen.getByText("Application filled to review")).toBeInTheDocument();
  expect(screen.getByText(/1 of 1; 95% interval 20%–100%/)).toBeInTheDocument();
  expect(screen.getByText("greenhouse")).toBeInTheDocument();
  expect(screen.getByText(/needs more replies/)).toBeInTheDocument();
});

test("the warning goes away once there are enough applications", async () => {
  mockFetch({ "/api/analytics": { ...ANALYTICS, totals: { ...ANALYTICS.totals, submitted: 40 } } });
  render(<Analytics />);
  await screen.findAllByText("3,200");
  expect(screen.queryByText(/Only .* submitted so far/)).toBeNull();
});

test("company names from the web are shown as text", async () => {
  mockFetch({ "/api/analytics": ANALYTICS });
  const { container } = render(<Analytics />);
  expect(await screen.findByText("<b>Acme</b>")).toBeInTheDocument();
  expect(container.querySelector("b > b")).toBeNull();
});

test("with nothing discovered it says so instead of drawing empty charts", async () => {
  mockFetch({ "/api/analytics": { ...ANALYTICS, funnel: [] } });
  render(<Analytics />);
  expect(await screen.findByText(/No data yet/)).toBeInTheDocument();
});

test("applications and discovery are drawn on separate charts so the large numbers do not flatten the small ones", async () => {
  mockFetch({ "/api/analytics": ANALYTICS });
  render(<Analytics />);
  await screen.findAllByText("3,200");
  expect(screen.getByText("Your applications")).toBeInTheDocument();
  expect(screen.getByText("Postings found", { selector: "h3" })).toBeInTheDocument();
  const charts = screen.getAllByRole("img", { name: /Daily activity/ });
  expect(charts).toHaveLength(2);
});
