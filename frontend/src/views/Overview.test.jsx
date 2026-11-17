import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Overview from "./Overview.jsx";
import { discoverySegments } from "./overview/Discovery.jsx";
import { ANALYTICS, JOB, mockFetch } from "../test/mockFetch.js";

const OV = { jobs: {}, applications: {}, outcomes: {}, waiting: 2, ready: 1, questions: 3, runs_active: 1, jobs_active: 1 };
const CONFIG = { read_only: false, novnc: "" };

function show(analytics = ANALYTICS, pipeline = [JOB]) {
  mockFetch({ "/api/analytics": analytics, "/api/pipeline": pipeline });
  return render(<Overview overview={OV} config={CONFIG} />);
}

test("the overview carries what needs you, what is running, and the numbers behind the pipeline, in one page", async () => {
  show();
  expect(await screen.findByText("Robotics Engineer")).toBeInTheDocument();             // a running job
  expect(await screen.findByText("From posting to answer")).toBeInTheDocument();
  for (const h of ["Working now", "Discovery", "Outcomes", "Resume scores"]) expect(screen.getByRole("heading", { name: h })).toBeInTheDocument();
  expect(screen.getByText(/Activity, last 2 days/)).toBeInTheDocument();
  expect(screen.getByText("questions waiting for you")).toBeInTheDocument();
  expect(screen.getAllByText("3,105").length).toBeGreaterThan(0);
});

test("few applications means a warning, enough means none", async () => {
  const { unmount } = show();
  expect(await screen.findByText(/Only 4 applications submitted so far/)).toBeInTheDocument();
  unmount();
  show({ ...ANALYTICS, totals: { ...ANALYTICS.totals, submitted: 40 } });
  await screen.findByText("From posting to answer");
  expect(screen.queryByText(/Only .* submitted so far/)).toBeNull();
});

test("where the postings went adds up to every posting found, and the four panels under it are a matching row", async () => {
  expect(discoverySegments(ANALYTICS).reduce((s, x) => s + x.n, 0)).toBe(ANALYTICS.totals.jobs);
  const { container } = show();
  await screen.findByText(/Where the 3,105 postings went/);
  const row = container.querySelector(".grid4");
  expect(row.children).toHaveLength(4);
  expect([...row.querySelectorAll("h3")].map((h) => h.textContent)).toEqual(["Outside the US: top places", "Companies with the most postings", "Roles of the kept postings", "Title match score"]);
  expect(container.querySelectorAll(".stack .seg").length).toBe(discoverySegments(ANALYTICS).length);
});

test("the legend names each part with its count and share", async () => {
  show();
  const legend = (await screen.findByText("Outside the United States", { selector: ".sl" })).closest("li");
  expect(within(legend).getByText("400")).toBeInTheDocument();
  expect(within(legend).getByText("13%")).toBeInTheDocument();
});

test("reply rates can be viewed by portal, role or score band", async () => {
  show();
  expect(await screen.findByText("greenhouse")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Role" }));
  expect(screen.getByText("swe", { selector: "td" })).toBeInTheDocument();
  expect(screen.queryByText("greenhouse")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Resume score" }));
  expect(screen.getByText("Nothing here yet.")).toBeInTheDocument();
});

test("company names from the web are shown as text", async () => {
  const { container } = show();
  expect(await screen.findByText("<b>Acme</b>")).toBeInTheDocument();
  expect(container.querySelector("b > b")).toBeNull();
});

test("with nothing discovered it says so, and without any analytics response it still shows the running jobs", async () => {
  const { unmount } = show({ ...ANALYTICS, funnel: [] });
  expect(await screen.findByText(/No numbers yet/)).toBeInTheDocument();
  unmount();
  mockFetch({ "/api/pipeline": [JOB] });                                                // /api/analytics answers 404
  render(<Overview overview={OV} config={CONFIG} />);
  expect(await screen.findByText("Robotics Engineer")).toBeInTheDocument();
});
