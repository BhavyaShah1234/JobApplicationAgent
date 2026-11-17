import { ago, dur, iteration, kind, parse } from "./format.js";

test("times are read as UTC and shown relative to now", () => {
  const now = Date.parse("2026-11-17T10:00:00Z");
  expect(parse("2026-11-17 09:00:00+00:00").toISOString()).toBe("2026-11-17T09:00:00.000Z");
  expect(parse("garbage")).toBeNull();
  expect(ago("2026-11-17T09:59:40Z", now)).toBe("just now");
  expect(ago("2026-11-17T09:30:00Z", now)).toBe("30 min ago");
  expect(ago("2026-11-17T07:00:00Z", now)).toBe("3 h ago");
  expect(ago("2026-11-15T10:00:00Z", now)).toBe("2 d ago");
  expect(ago("", now)).toBe("");
});

test("durations", () => {
  expect(dur(45)).toBe("45s");
  expect(dur(840)).toBe("14 min");
  expect(dur(7200)).toBe("2.0 h");
});

test("a status word picks its colour family", () => {
  expect(kind("failed")).toBe("bad");
  expect(kind("needs_review")).toBe("warn");
  expect(kind("running")).toBe("run");
  expect(kind("confirmed")).toBe("ok");
  expect(kind("")).toBe("");
});

test("iterations read as 'i of n'", () => {
  expect(iteration({ i: 2, n: 3 })).toBe("2 of 3");
  expect(iteration({ i: 4, n: null })).toBe("4");
  expect(iteration({ i: null, n: null })).toBe("");
});
